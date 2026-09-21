import asyncio
from contextlib import asynccontextmanager
from uuid import uuid4

import anyio
import pytest
from mcp.server.lowlevel import Server

from evoagent.config import Settings
from evoagent.mcp.connections import ConnectionManager
from evoagent.mcp.schema import MCPError, MCPServerConfig


def config(**kwargs):
    return MCPServerConfig(
        name="fixture", transport="stdio", launch_profile_id="fixture", enabled=True, **kwargs
    )


@pytest.mark.parametrize("mode", ["disconnect", "timeout", "cancel"])
async def test_bounded_reconnect_and_cancel_cleanup(mode):
    attempts, closed, observations = [], [], []

    @asynccontextmanager
    async def transport(_config, _settings):
        attempts.append(1)
        try:
            if mode == "disconnect":
                raise OSError("raw-secret-must-not-escape")
            await asyncio.Event().wait()
            yield  # pragma: no cover
        finally:
            closed.append(1)

    async def publish(*_):
        raise AssertionError("no catalog")

    async def observe(state, error):
        observations.append((state, error))

    manager = ConnectionManager(Settings(), transport=transport)
    task = asyncio.create_task(
        manager.discover(uuid4(), 0, config(connection_timeout=0.1), publish, observe)
    )
    try:
        if mode == "cancel":
            await asyncio.sleep(0.03)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert len(attempts) == 1
        else:
            with pytest.raises(MCPError, match="transport_failed|timeout"):
                await task
            assert len(attempts) == 2
            assert observations[-1][0] == "degraded"
        assert attempts == closed
        assert not manager.connections
        assert "raw-secret" not in str(observations)
    finally:
        await manager.aclose()


async def test_unsupported_tools_capability_uses_real_sdk_handshake():
    observations = []
    server = Server("no-tools", version="1")

    @asynccontextmanager
    async def transport(_config, _settings):
        client_send, server_receive = anyio.create_memory_object_stream(0)
        server_send, client_receive = anyio.create_memory_object_stream(0)
        async with anyio.create_task_group() as group:
            group.start_soon(
                server.run, server_receive, server_send, server.create_initialization_options()
            )
            try:
                yield client_receive, client_send
            finally:
                group.cancel_scope.cancel()

    async def publish(*_):
        raise AssertionError("unsupported capability cannot publish")

    async def observe(state, code):
        observations.append((state, code))

    manager = ConnectionManager(Settings(), transport=transport)
    try:
        with pytest.raises(MCPError, match="mcp_tools_unsupported"):
            await manager.discover(uuid4(), 0, config(), publish, observe)
        assert observations[-1] == ("degraded", "mcp_tools_unsupported")
    finally:
        await manager.aclose()
