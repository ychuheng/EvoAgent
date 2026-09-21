import asyncio
import json
import os
import socket
import sys
from contextlib import asynccontextmanager

import httpx
import pytest
import uvicorn
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from sqlalchemy import select
from starlette.applications import Starlette
from starlette.routing import Route

from evoagent.api.app import create_app
from evoagent.config import Settings
from evoagent.db.base import Base
from evoagent.db.models import (
    MCPCatalogRecord,
    MCPHealthRecord,
    MCPServerRecord,
    MCPToolReviewRecord,
)
from evoagent.db.session import Database
from evoagent.mcp.connections import ConnectionManager
from evoagent.mcp.fixture import build_server
from evoagent.mcp.schema import MCPError, MCPServerConfig, ServerUpdate, ToolReview
from evoagent.mcp.service import MCPService


def process_exists(pid):
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        try:
            code = wintypes.DWORD()
            return bool(kernel.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def write_state(path, revision, name="echo", **extras):
    temporary = path.with_suffix(".new")
    temporary.write_text(
        json.dumps(
            {
                "revision": revision,
                "tools": [
                    {
                        "name": name,
                        "inputSchema": {"type": "object"},
                        "annotations": {"readOnlyHint": True},
                    },
                    {"name": "health", "inputSchema": {"type": "object"}},
                ],
                **extras,
            }
        ),
        encoding="utf-8",
    )
    temporary.replace(path)


@pytest.fixture
async def environment(tmp_path):
    state = tmp_path / "fixture.json"
    write_state(state, 1)
    db = Database(f"sqlite+aiosqlite:///{tmp_path / 'mcp.db'}")
    async with db.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    settings = Settings(
        workspace=tmp_path / "workspace",
        mcp_launch_profiles={
            "fixture": {
                "command": sys.executable,
                "args": [
                    "-m",
                    "evoagent.mcp.fixture",
                    "--state-file",
                    str(state),
                    "--pid-file",
                    str(state.with_suffix(".pid")),
                ],
                "trusted_fixture": True,
            },
        },
    )
    manager = ConnectionManager(settings)
    service = MCPService(db.session_factory, settings, manager)
    try:
        yield db, settings, manager, service, state
    finally:
        await manager.aclose()
        await db.dispose()


async def create_enabled(service):
    return await service.create(
        MCPServerConfig(
            name="fixture", transport="stdio", launch_profile_id="fixture", enabled=True
        )
    )


async def test_real_stdio_pagination_reviews_and_lifecycle(environment):
    db, _, manager, service, state = environment
    server = await create_enabled(service)
    first = await service.discover(server["id"])
    assert [t["name"] for t in first["tools"]] == ["echo", "health"]
    assert not first["execution_enabled"]
    assert (await service.discover(server["id"]))["id"] == first["id"]
    async with db.session_factory() as session:
        reviews = list(await session.scalars(select(MCPToolReviewRecord)))
        assert all(not row.approved and row.risk == "R3" for row in reviews)
        assert (await session.scalar(select(MCPHealthRecord))).state == "ready"
    decision = ToolReview(
        expected_lock_version=0,
        tool_name="echo",
        approved=True,
        risk="R0",
        effect="read_only",
        reviewer="local",
        reason="trusted fixture",
    )
    assert (await service.review(first["id"], decision))["lock_version"] == 1
    with pytest.raises(MCPError, match="review_conflict"):
        await service.review(first["id"], decision)
    connection = manager.connections[server["id"]][1]
    pid = int(state.with_suffix(".pid").read_text(encoding="ascii"))
    assert process_exists(pid)
    await manager.disconnect(server["id"])
    assert connection.task.done() and connection.state == "disabled"
    assert not process_exists(pid)
    assert not manager.connections


async def test_list_changed_creates_new_unreviewed_catalog(environment):
    db, _, _, service, state = environment
    server = await create_enabled(service)
    first = await service.discover(server["id"])
    await service.review(
        first["id"],
        ToolReview(
            expected_lock_version=0,
            tool_name="health",
            approved=True,
            risk="R0",
            effect="read_only",
            reviewer="local",
            reason="known",
        ),
    )
    write_state(state, 2, "renamed")
    async with asyncio.timeout(8):
        while True:
            async with db.session_factory() as session:
                latest = await session.scalar(
                    select(MCPCatalogRecord).order_by(MCPCatalogRecord.revision.desc())
                )
                if latest.revision == 2:
                    break
            await asyncio.sleep(0.05)
    assert latest.diff == {"added": ["renamed"], "removed": ["echo"], "changed": []}
    async with db.session_factory() as session:
        rows = list(
            await session.scalars(
                select(MCPToolReviewRecord).where(MCPToolReviewRecord.catalog_id == latest.id)
            )
        )
        assert all(not row.approved and row.lock_version == 0 for row in rows)


async def test_disable_and_late_catalog_are_fenced(environment):
    db, _, manager, service, _ = environment
    from mcp import types

    server = await create_enabled(service)
    await service.discover(server["id"])
    config = MCPServerConfig.model_validate(server["config"]).model_copy(update={"enabled": False})
    await service.update(server["id"], ServerUpdate(config=config, expected_lock_version=0))
    assert not manager.connections
    with pytest.raises(MCPError, match="disabled"):
        await service.discover(server["id"])
    with pytest.raises(MCPError, match="config_conflict"):
        await service.update(server["id"], ServerUpdate(config=config, expected_lock_version=0))
    initialization = types.InitializeResult(
        protocolVersion="2025-11-25",
        capabilities=types.ServerCapabilities(tools=types.ToolsCapability()),
        serverInfo=types.Implementation(name="old", version="1"),
    )
    with pytest.raises(MCPError, match="config_changed"):
        await service.publish(server["id"], 0, (), initialization)
    async with db.session_factory() as session:
        assert (await session.get(MCPServerRecord, server["id"])).latest_revision == 1


async def test_bad_catalog_never_partially_persists(environment):
    db, _, _, service, state = environment
    write_state(state, 1, "health")  # duplicate names across pages
    server = await create_enabled(service)
    with pytest.raises(MCPError):
        await service.discover(server["id"])
    async with db.session_factory() as session:
        assert await session.scalar(select(MCPCatalogRecord)) is None
        assert (await session.scalar(select(MCPHealthRecord))).state == "degraded"


async def test_catalog_and_review_evidence_are_immutable(environment):
    db, _, _, service, _ = environment
    server = await create_enabled(service)
    first = await service.discover(server["id"])
    async with db.session_factory() as session:
        row = await session.get(MCPCatalogRecord, first["id"])
        row.tools = []
        with pytest.raises(ValueError, match="immutable"):
            await session.commit()


async def test_config_cas_rejects_concurrent_same_version(environment):
    _, _, _, service, _ = environment
    server = await create_enabled(service)
    request = ServerUpdate(
        expected_lock_version=0,
        config=MCPServerConfig.model_validate(server["config"]).model_copy(
            update={"enabled": False}
        ),
    )
    results = await asyncio.gather(
        service.update(server["id"], request),
        service.update(server["id"], request),
        return_exceptions=True,
    )
    assert sum(isinstance(item, dict) for item in results) == 1
    assert any(
        isinstance(item, MCPError) and item.code == "mcp_config_conflict" for item in results
    )


async def test_real_http_sdk_and_api_discovery(tmp_path):
    protocol = build_server()
    transport_manager = StreamableHTTPSessionManager(protocol, json_response=True)

    @asynccontextmanager
    async def lifespan(_app):
        async with transport_manager.run():
            yield

    class Endpoint:
        async def __call__(self, scope, receive, send):
            await transport_manager.handle_request(scope, receive, send)

    fixture = Starlette(
        routes=[Route("/mcp", endpoint=Endpoint(), methods=["GET", "POST", "DELETE"])],
        lifespan=lifespan,
    )
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(fixture, log_level="error", lifespan="on"))
    task = asyncio.create_task(server.serve(sockets=[sock]))
    async with asyncio.timeout(5):
        while not server.started:
            await asyncio.sleep(0.01)
    db = Database(f"sqlite+aiosqlite:///{tmp_path / 'http.db'}")
    async with db.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    settings = Settings(
        workspace=tmp_path / "workspace",
        mcp_http_profiles={
            "local_fixture": {"url": f"http://127.0.0.1:{port}/mcp", "local_fixture": True},
        },
    )
    app = create_app(settings, database=db)
    try:
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client,
        ):
            created = await client.post(
                "/api/v1/mcp/servers",
                json={
                    "name": "local",
                    "transport": "streamable_http",
                    "endpoint_profile_id": "local_fixture",
                    "enabled": True,
                },
            )
            assert created.status_code == 201
            identity = created.json()["id"]
            catalog = await client.post(f"/api/v1/mcp/servers/{identity}/discover")
            assert catalog.status_code == 200, catalog.text
            assert len(catalog.json()["tools"]) == 2
            before = catalog.json()["id"]
            assert (await client.get(f"/api/v1/mcp/servers/{identity}/catalogs")).json()[0][
                "id"
            ] == before
            assert (await client.get(f"/api/v1/mcp/servers/{identity}/health")).json()[0][
                "state"
            ] == "ready"
            assert (await client.post(f"/api/v1/mcp/servers/{identity}/call")).status_code == 404
            assert (
                await client.post(f"/api/v1/mcp/servers/{identity}/disconnect")
            ).status_code == 204
    finally:
        await db.dispose()
        server.should_exit = True
        await asyncio.wait_for(task, 5)
        sock.close()
