"""四条交付演示的连续业务场景；外部环境缺失明确跳过。"""

import asyncio
import json
import os
import socket
import sys
from uuid import uuid4

import pytest
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from test_mcp_catalogs import write_state
from test_mcp_execution import call, setup_execution
from test_memory_foundations import confirm, large_context, proposed

from evoagent.config import Settings
from evoagent.core.context_budget import ContextBudget, MockCounter
from evoagent.core.context_policy import BoundedContextPolicy, ContextPolicyError
from evoagent.core.models import LoopState, ModelRequest, TokenUsage
from evoagent.db.base import Base
from evoagent.db.models import ArtifactRecord, ContextRevisionRecord, SessionRecord, WorkspaceRecord
from evoagent.db.session import Database
from evoagent.mcp.adapter import register_run_tools
from evoagent.mcp.connections import ConnectionManager
from evoagent.mcp.schema import ExecutionUpdate
from evoagent.mcp.service import MCPService
from evoagent.memory.repository import bind_version, check_run_references
from evoagent.memory.schema import MemoryDecision, MemoryError
from evoagent.runtime.checkpoints import PersistentCheckpointStore
from evoagent.runtime.context_store import ContextStore
from evoagent.tasks.lease import JobLeaseManager, TaskExecutionResult
from evoagent.tasks.lease_guard import LeaseGuard
from evoagent.tasks.service import TaskService
from evoagent.tasks.state_machine import PersistentRunStatus
from evoagent.tools.registry import ToolRegistry
from evoagent.trace.artifacts import LocalArtifactStore
from evoagent.workers.rate_limit import RateLimited, ServiceGate
from evoagent.workers.wakeup import Wakeup


@pytest.fixture
async def demo_db(tmp_path):
    url = os.getenv("EVOAGENT_TEST_DATABASE_URL")
    if not url:
        pytest.skip("demo requires explicit PostgreSQL test database")
    schema = "demo_" + uuid4().hex
    admin = Database(url)
    async with admin.engine.begin() as connection:
        await connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public"))
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    db = Database(url)
    await db.engine.dispose()
    db.engine = create_async_engine(
        url, connect_args={"server_settings": {"search_path": f"{schema},public"}}
    )
    db.session_factory.configure(bind=db.engine)
    try:
        async with db.engine.begin() as connection:
            await connection.run_sync(lambda c: Base.metadata.create_all(c, checkfirst=False))
        yield db
    finally:
        await db.dispose()
        async with admin.engine.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await admin.dispose()


async def environment(db, tmp_path):
    tasks = TaskService(db.session_factory)
    scope = await tasks.create_session("phase four demo")
    return db, tasks, scope.id, LocalArtifactStore(tmp_path / "artifacts")


async def test_demo_context_restore_and_block(demo_db, tmp_path, record_property):
    db, tasks, sid, store = await environment(demo_db, tmp_path)
    task = await tasks.create_task(
        session_id=sid, goal="保留原始约束", provider="mock", model="mock"
    )
    lease = await JobLeaseManager(db.session_factory, lease_seconds=60).claim_next("demo")
    policy = BoundedContextPolicy(
        ContextBudget(context_window=25000, output_tokens=1000, safety_margin=0), MockCounter()
    )
    context = ContextStore(db.session_factory, LeaseGuard(lease), store, policy)
    state = LoopState(
        messages=large_context(),
        completed_iterations=5,
        usage=TokenUsage(input_tokens=10, output_tokens=5, total_tokens=15),
        repeated_tool_calls=2,
        previous_tool_fingerprint="demo-repeat",
        config_hash="demo",
    )
    request = ModelRequest(messages=state.messages, model="mock", tool_definitions=())
    compressed = await context.prepare(state, request)
    restored = await PersistentCheckpointStore(
        task.run.id, db.session_factory, schema_version=2
    ).load_latest()
    assert restored == compressed and restored.context_revision_id
    assert restored.completed_iterations == 5 and restored.repeated_tool_calls == 2
    assert restored.usage == state.usage and restored.messages[:2] == state.messages[:2]
    policy.prepare(request.model_copy(update={"messages": restored.messages}))
    async with db.session_factory() as session:
        revision = await session.get(ContextRevisionRecord, restored.context_revision_id)
        artifact = await session.get(ArtifactRecord, revision.artifact_id)
        await store.erase(artifact.uri)
    with pytest.raises(ContextPolicyError) as failure:
        await context.prepare(restored, request.model_copy(update={"messages": restored.messages}))
    assert failure.value.code == "context_artifact_invalid"
    record_property(
        "evidence",
        json.dumps(
            {
                "revision": revision.revision,
                "estimate": revision.estimate,
                "restored_iterations": restored.completed_iterations,
                "preserved_tokens": restored.usage.total_tokens,
                "blocked": failure.value.code,
            }
        ),
    )


async def test_demo_cross_session_memory_and_revoke(demo_db, tmp_path, record_property):
    env = await environment(demo_db, tmp_path)
    db, tasks, sid, _ = env
    service, entry, version, _, _ = await proposed(env, scope="workspace")
    entry, version = await confirm(service, sid, entry, version)
    same = await tasks.create_session("same workspace, different session")
    assert len(await service.list(same.id, query="中文")) == 1
    async with db.session_factory() as session:
        workspace = WorkspaceRecord(name="foreign")
        session.add(workspace)
        await session.flush()
        foreign = SessionRecord(title="foreign", workspace_id=workspace.id)
        session.add(foreign)
        await session.commit()
    assert await service.list(foreign.id, query="中文") == []
    task = await tasks.create_task(session_id=same.id, goal="继续", provider="mock", model="mock")
    async with db.session_factory() as session:
        await bind_version(session, task.run.id, version.id)
        await session.commit()
    await service.decide(
        sid, version.id, MemoryDecision(action="revoke", expected_lock_version=entry.lock_version)
    )
    assert await service.list(same.id, query="中文") == []
    async with db.session_factory() as session:
        with pytest.raises(MemoryError, match="context_source_revoked"):
            await check_run_references(session, task.run.id)
    record_property(
        "evidence",
        json.dumps(
            {
                "same_workspace_before": 1,
                "foreign_workspace": 0,
                "same_workspace_after": 0,
                "bound_run_blocked": "context_source_revoked",
            }
        ),
    )


async def test_demo_mcp_call_change_and_unload(demo_db, tmp_path, record_property):
    state = tmp_path / "fixture.json"
    settings = Settings(
        _env_file=None,
        workspace=tmp_path,
        mcp_launch_profiles={
            "fixture": {
                "command": sys.executable,
                "args": ["-m", "evoagent.mcp.fixture", "--state-file", str(state)],
                "trusted_fixture": True,
            }
        },
    )
    manager = ConnectionManager(settings)
    service = MCPService(demo_db.session_factory, settings, manager)
    try:
        server, catalog, aggregate, guard, registry, adapter, executor, log = await setup_execution(
            (demo_db, settings, manager, service, state)
        )
        assert (await executor.execute(call(adapter, text="hello"))).error_code is None
        before = registry.manifest_hash()
        write_state(state, 2, name="changed", call_log=str(log))
        await service.discover(server["id"])
        recovered = ToolRegistry()
        await register_run_tools(service, recovered, aggregate.run.id, guard)
        assert recovered.manifest_hash() == before
        changed = await executor.execute(call(adapter, "after-change"))
        assert changed.error_code == "tool_manifest_changed"
        await service.set_execution(
            server["id"],
            ExecutionUpdate(
                expected_lock_version=0, expected_execution_version=1, state="disabled"
            ),
        )
        disabled = await executor.execute(call(adapter, "after-disable"))
        assert disabled.error_code == "mcp_server_disabled"
        assert len(log.read_text().splitlines()) == 1
        record_property(
            "evidence",
            json.dumps(
                {
                    "catalog_revision": catalog["revision"],
                    "remote_calls": 1,
                    "frozen_manifest_unchanged": True,
                    "changed_catalog_blocked": changed.error_code,
                    "unloaded_blocked": disabled.error_code,
                }
            ),
        )
    finally:
        await manager.aclose()


async def test_demo_real_redis_outage_keeps_db_task(demo_db, tmp_path, record_property):
    # 占住但不监听的 loopback 端口产生真实连接拒绝，不停止共享 Redis 服务。
    with socket.socket() as endpoint:
        endpoint.bind(("127.0.0.1", 0))
        client = Redis(
            host="127.0.0.1",
            port=endpoint.getsockname()[1],
            socket_connect_timeout=0.1,
            socket_timeout=0.1,
        )
        wakeup = Wakeup(client, "demo-outage")
        demo_db.session_factory.configure(info={"wakeup": wakeup})
        try:
            _, tasks, sid, _ = await environment(demo_db, tmp_path)
            task = await tasks.create_task(
                session_id=sid, goal="Redis 断线任务不丢失", provider="mock", model="mock"
            )
            await asyncio.wait_for(wakeup.wait(asyncio.Event(), 0.01), 1)
            lease = await JobLeaseManager(demo_db.session_factory, lease_seconds=60).claim_next(
                "polling-worker"
            )
            assert lease.task_id == task.task.id
            gate = ServiceGate(Settings(_env_file=None, rate_wait_seconds=0.1), client)
            with pytest.raises(RateLimited):
                async with gate.acquire("model:demo"):
                    pytest.fail("must not dispatch external request during quota outage")
            await JobLeaseManager(demo_db.session_factory, lease_seconds=60).finalize(
                lease,
                TaskExecutionResult(
                    status=PersistentRunStatus.FAILED, error_code="demo_quota_unavailable"
                ),
            )
            record_property(
                "evidence",
                json.dumps(
                    {
                        "database_task_claimed": True,
                        "notification_failure_tolerated": True,
                        "external_dispatches": 0,
                        "fault": "real loopback connection refused",
                        "terminal": "failed",
                    }
                ),
            )
        finally:
            demo_db.session_factory.configure(info={})
            await client.aclose()
