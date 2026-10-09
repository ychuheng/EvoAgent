import asyncio
import base64
import json
import sys
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select

from evoagent.config import Settings
from evoagent.db.base import Base
from evoagent.db.models import ArtifactRecord, SandboxExecutionRecord, TaskRecord
from evoagent.db.session import Database
from evoagent.sandbox.client import ControllerExecutor, DisabledSandboxExecutor
from evoagent.sandbox.controller import create_controller
from evoagent.sandbox.docker import SandboxError, bounded_output
from evoagent.sandbox.schema import SandboxRequest, SandboxSpec
from evoagent.sandbox.service import SandboxService, decode_outputs
from evoagent.skills.canonical import content_hash
from evoagent.tasks.lease import JobLeaseManager
from evoagent.tools.base import ToolPermissionError

IMAGE = "example/sandbox@sha256:" + "a" * 64


class FakeDriver:
    """只执行固定测试脚本；没有根据待测 argv 执行宿主命令。"""

    def __init__(self):
        self.created = []
        self.removed = []
        self.names = set()
        self.delay = 0
        self.payload = {
            "return_code": 0,
            "stdout": "ok",
            "stderr": "",
            "files": [{"name": "result.txt", "data": "aGVsbG8="}],
        }
        self.fail_cleanup = False

    async def create(self, name, spec, input_path, argv, **kwargs):
        self.names.add(name)
        self.created.append((name, spec, input_path, argv))
        return "container-id"

    async def start(self, name, **kwargs):
        script = "import time,sys; time.sleep(float(sys.argv[1])); print(sys.argv[2])"
        return await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            script,
            str(self.delay),
            json.dumps(self.payload),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

    async def remove(self, name):
        if self.fail_cleanup:
            raise SandboxError("sandbox_cleanup_failed")
        self.removed.append(name)
        self.names.discard(name)

    async def managed(self):
        return list(self.names)


@pytest.fixture
async def sandbox_env(tmp_path):
    db = Database(f"sqlite+aiosqlite:///{tmp_path / 'sandbox.db'}")
    async with db.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    settings = Settings(
        workspace=tmp_path,
        artifact_root=tmp_path / "artifacts",
        sandbox_staging_root=tmp_path / "stage",
        sandbox_profiles={"python": SandboxSpec(image=IMAGE)},
        shell_sandbox_profile="python",
        sandbox_controller_url="http://controller",
        sandbox_controller_token="x" * 32,
    )
    from evoagent.tasks.service import TaskService

    service = TaskService(db.session_factory)
    session = await service.create_session("sandbox")
    aggregate = await service.create_task(
        session_id=session.id, goal="test", provider="mock", model="mock"
    )
    lease = await JobLeaseManager(db.session_factory, lease_seconds=120).claim_next("sandbox-test")
    driver = FakeDriver()
    request = SandboxRequest(
        execution_id=uuid4(),
        task_id=aggregate.task.id,
        run_id=aggregate.run.id,
        owner=lease.owner,
        epoch=lease.epoch,
        profile="python",
        profile_hash=content_hash(settings.sandbox_profiles["python"].model_dump(mode="json")),
        argv=("/usr/local/bin/python", "-c", "print('hello')"),
    )
    try:
        yield db, settings, driver, request, lease
    finally:
        await db.dispose()


async def test_authenticated_client_controller_artifact_roundtrip(sandbox_env):
    db, settings, driver, request, lease = sandbox_env
    app = create_controller(settings, db, driver)
    async with app.router.lifespan_context(app):
        executor = ControllerExecutor(settings, lease, transport=httpx.ASGITransport(app=app))
        result = await executor.run(request.argv)
    assert result.stdout == "ok" and len(result.artifacts) == 1
    assert len(driver.created) == len(driver.removed) == 1
    async with db.session_factory() as session:
        row = await session.scalar(select(SandboxExecutionRecord))
        artifact = await session.scalar(select(ArtifactRecord))
        assert row.status == "succeeded" and row.lease_epoch == lease.epoch
        assert artifact.attributes["lease_epoch"] == lease.epoch
        assert (settings.artifact_root / artifact.uri).read_bytes() == b"hello"
    assert not driver.names


async def test_missing_auth_and_unknown_request_fields_rejected(sandbox_env):
    db, settings, driver, request, _ = sandbox_env
    app = create_controller(settings, db, driver)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        assert (
            await client.post("/executions", json=request.model_dump(mode="json"))
        ).status_code == 401
        body = {**request.model_dump(mode="json"), "mounts": ["/:/host"]}
        response = await client.post(
            "/executions", json=body, headers={"Authorization": "Bearer " + "x" * 32}
        )
        assert response.status_code == 422
    assert not driver.created


@pytest.mark.parametrize(
    "change,code",
    [
        ({"profile_hash": "wrong"}, "profile_changed"),
        ({"argv": ("/bin/sh",)}, "executable_forbidden"),
        ({"epoch": 999}, "lease"),
    ],
)
async def test_profile_and_lease_fail_before_container(sandbox_env, change, code):
    db, settings, driver, request, _ = sandbox_env
    service = SandboxService(settings, db.session_factory, driver)
    with pytest.raises(Exception, match=code):
        await service.run(request.model_copy(update=change))
    assert not driver.created


@pytest.mark.parametrize("reason", ["cancel", "lease", "timeout", "cleanup"])
async def test_running_job_cleanup_and_failure_evidence(sandbox_env, reason):
    db, settings, driver, request, _ = sandbox_env
    if reason == "timeout":
        settings.sandbox_profiles["python"] = settings.sandbox_profiles["python"].model_copy(
            update={"timeout_seconds": 1}
        )
        request = request.model_copy(
            update={
                "profile_hash": content_hash(
                    settings.sandbox_profiles["python"].model_dump(mode="json")
                )
            }
        )
    driver.delay = 2
    service = SandboxService(settings, db.session_factory, driver)
    running = asyncio.create_task(service.run(request))
    for _ in range(100):
        if driver.created:
            break
        await asyncio.sleep(0.01)
    # 如果把"容器已创建"当成默认事实，机器繁忙时循环会超时退出，随后 `cancel` 会取消一个
    # 还没启动的协程，最终表现为一条看不懂的状态断言失败。这里显式失败并说明原因。
    assert driver.created, (
        "等待容器创建超时（1 秒）；这是测试时序问题，不是产品缺陷——在更慢的机器上需要放宽这个窗口"
    )
    if reason == "cancel":
        running.cancel()
    elif reason == "lease":
        async with db.session_factory() as session:
            task = await session.get(TaskRecord, request.task_id)
            task.lease_epoch += 1
            await session.commit()
    elif reason == "cleanup":
        driver.fail_cleanup = True
    with pytest.raises((asyncio.CancelledError, SandboxError)):
        await running
    async with db.session_factory() as session:
        row = await session.get(SandboxExecutionRecord, request.execution_id)
        assert row.status == (
            "cleanup_pending"
            if reason == "cleanup"
            else "cancelled"
            if reason == "cancel"
            else "failed"
        )
        assert await session.scalar(select(ArtifactRecord)) is None
    if reason == "cleanup":
        driver.fail_cleanup = False
        await service.reap()
    assert not driver.names


async def test_cancel_during_connection_ping_releases_reader_before_cleanup(
    sandbox_env, monkeypatch
):
    from sqlalchemy.util.concurrency import await_only

    db, settings, driver, request, lease = sandbox_env
    service = SandboxService(settings, db.session_factory, driver)
    started, release = asyncio.Event(), asyncio.Event()
    original_ping = db.engine.dialect.do_ping
    gated = False

    def ping(connection):
        nonlocal gated
        if gated:
            return original_ping(connection)
        gated = True
        cursor = connection.cursor()
        cursor.execute("SELECT id FROM tasks")
        cursor.fetchone()
        started.set()
        # Like the dialect's ping, close follows awaited driver I/O. Cancellation
        # here must not strand a cursor holding a SQLite read lock.
        await_only(release.wait())
        cursor.close()
        return True

    monkeypatch.setattr(db.engine.dialect, "do_ping", ping)
    checking = asyncio.create_task(service.check(service.guard(request)))
    await asyncio.wait_for(started.wait(), 2)
    checking.cancel()
    try:
        await asyncio.sleep(0.05)
        assert not checking.done(), "cancel escaped before the checked-out reader closed"
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await checking
    async with db.session_factory() as session:
        task = await session.get(TaskRecord, lease.task_id)
        task.cancel_requested = True
        await session.commit()


async def test_input_must_belong_to_run_and_is_readonly_staged(sandbox_env):
    db, settings, driver, request, _ = sandbox_env
    service = SandboxService(settings, db.session_factory, driver)
    with pytest.raises(SandboxError, match="input_forbidden"):
        await service.run(request.model_copy(update={"input_artifact_ids": (uuid4(),)}))
    assert not driver.created
    from evoagent.trace.artifacts import ArtifactService, LocalArtifactStore

    artifact = await ArtifactService(
        LocalArtifactStore(settings.artifact_root), db.session_factory
    ).create(run_id=request.run_id, name="input.txt", content=b"input", artifact_type="test")
    staged_request = request.model_copy(
        update={"execution_id": uuid4(), "input_artifact_ids": (artifact.id,)}
    )
    stage = await service.stage(staged_request, service.guard(request))
    assert (stage / str(artifact.id)).read_bytes() == b"input"
    service.erase_stage(staged_request)


async def test_orphan_record_is_reaped_without_replaying(sandbox_env):
    db, settings, driver, request, _ = sandbox_env
    service = SandboxService(settings, db.session_factory, driver)
    await service.record(
        request.execution_id,
        settings.sandbox_profiles["python"],
        run_id=request.run_id,
        epoch=request.epoch,
    )
    driver.names.add(f"evoagent-sandbox-{request.execution_id}")
    await service.reap()
    async with db.session_factory() as session:
        assert (await session.get(SandboxExecutionRecord, request.execution_id)).status == "reaped"
    assert not driver.created and not driver.names


async def test_output_flood_is_bounded_while_reading():
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        "import os;\nwhile True: os.write(1, b'x' * 16384)",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    with pytest.raises(SandboxError, match="output_limit"):
        await bounded_output(process, 32768)
    assert process.returncode is not None


async def test_shell_disabled_has_no_host_fallback():
    with pytest.raises(ToolPermissionError):
        await DisabledSandboxExecutor().run((sys.executable, "-c", "print(1)"))


@pytest.mark.parametrize(
    "name", ["../outside", "/etc/passwd", "a/b", "a\\b", "file:stream", ".", ".."]
)
def test_output_path_escape_rejected(name):
    with pytest.raises(SandboxError, match="artifact_path"):
        decode_outputs({"files": [{"name": name, "data": ""}]})


def test_output_file_and_total_limits():
    with pytest.raises(SandboxError, match="file_limit"):
        decode_outputs({"files": [{}] * 33})
    with pytest.raises(SandboxError, match="artifact_size"):
        decode_outputs(
            {"files": [{"name": "big", "data": base64.b64encode(b"x" * 1048577).decode()}]}
        )


async def test_authenticated_stdio_control_channel_with_real_mcp_sdk(sandbox_env):
    import socket

    import uvicorn

    from evoagent.mcp.connections import ConnectionManager
    from evoagent.mcp.schema import LaunchProfile, MCPServerConfig
    from evoagent.mcp.service import MCPService

    db, settings, _, _, _ = sandbox_env

    class ProtocolDriver(FakeDriver):
        async def start(self, name, **kwargs):
            return await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "evoagent.mcp.fixture",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

    driver = ProtocolDriver()
    settings.sandbox_profiles = {
        "mcp": SandboxSpec(
            image=IMAGE, mode="stdio", stdio_command=("/usr/local/bin/python", "-m", "fixture")
        )
    }
    settings.mcp_launch_profiles = {"container": LaunchProfile(sandbox_profile_id="mcp")}
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    settings.sandbox_controller_url = f"http://127.0.0.1:{listener.getsockname()[1]}"
    server = uvicorn.Server(
        uvicorn.Config(
            create_controller(settings, db, driver),
            log_level="critical",
            ws="websockets-sansio",
            lifespan="on",
        )
    )
    hosting = asyncio.create_task(server.serve(sockets=[listener]))
    manager = ConnectionManager(settings)
    try:
        for _ in range(200):
            if server.started:
                break
            await asyncio.sleep(0.01)
        assert server.started
        service = MCPService(db.session_factory, settings, manager)
        identity = await service.create(
            MCPServerConfig(
                name="isolated", transport="stdio", launch_profile_id="container", enabled=True
            )
        )
        catalog = await service.discover(identity["id"])
        assert [tool["name"] for tool in catalog["tools"]] == ["fixture_echo", "fixture_health"]
        assert len(driver.created) == 1
        await manager.aclose()
        for _ in range(100):
            if not driver.names:
                break
            await asyncio.sleep(0.01)
        assert not driver.names
    finally:
        await manager.aclose()
        server.should_exit = True
        await asyncio.wait_for(hosting, 10)
        listener.close()


async def test_nonzero_command_records_failure(sandbox_env):
    db, settings, driver, request, lease = sandbox_env
    driver.payload.update(return_code=2, files=[])
    service = SandboxService(settings, db.session_factory, driver)
    result = await service.run(request)
    assert result["return_code"] == 2
    async with db.session_factory() as session:
        row = await session.get(SandboxExecutionRecord, request.execution_id)
        assert row.status == "failed"
        assert row.error_code == "sandbox_command_failed"
    assert not driver.names


def test_controller_lock_rejects_second_owner_and_releases(tmp_path):
    from evoagent.sandbox.ownership import ControllerLock

    first, second = ControllerLock(tmp_path), ControllerLock(tmp_path)
    first.acquire()
    try:
        with pytest.raises(RuntimeError):
            second.acquire()
    finally:
        first.close()
    second = ControllerLock(tmp_path)
    second.acquire()
    second.close()
