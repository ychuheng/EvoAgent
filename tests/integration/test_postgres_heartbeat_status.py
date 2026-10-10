import asyncio
import os
from uuid import uuid4

import pytest
from sqlalchemy import event, select, text
from sqlalchemy.ext.asyncio import create_async_engine

from evoagent.config import Settings
from evoagent.core.context import ContextBuilder
from evoagent.core.models import ProviderEvent, ProviderEventType
from evoagent.db.base import Base
from evoagent.db.models import TaskRecord, ToolCallRecord
from evoagent.db.session import Database
from evoagent.memory.maintenance import MaintenanceWorker
from evoagent.runtime.persistent_runner import PersistentAgentRunner
from evoagent.tasks.lease import JobLeaseManager, TaskCancellationRequested
from evoagent.tasks.service import TaskService
from evoagent.tasks.state_machine import TaskStatus
from evoagent.tools.registry import ToolRegistry
from evoagent.trace.artifacts import LocalArtifactStore
from evoagent.workers.cancellation import CancellationNotifier
from evoagent.workers.heartbeat import LeaseHeartbeat
from evoagent.workers.main import JobWorker
from evoagent.workers.wakeup import Wakeup


@pytest.fixture
async def pg_task():
    url = os.getenv("EVOAGENT_TEST_DATABASE_URL")
    if not url:
        pytest.skip("requires isolated PostgreSQL")
    schema = "heartbeat_contract_" + uuid4().hex
    db = Database(url)
    async with db.engine.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    await db.dispose()
    options = {"search_path": f"{schema},public", "application_name": schema}
    db.engine = create_async_engine(
        url,
        connect_args={"server_settings": options},
        execution_options={"schema_translate_map": {None: schema}},
    )
    db.session_factory.configure(bind=db.engine)
    async with db.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    service = TaskService(db.session_factory)
    chat = await service.create_session("notification contract")
    task = await service.create_task(
        session_id=chat.id, goal="offline cancellation", provider="mock", model="mock"
    )
    manager = JobLeaseManager(db.session_factory, lease_seconds=60)
    lease = await manager.claim_next("owner")
    notifier = CancellationNotifier(db.session_factory, server_settings=options)
    stopping = asyncio.Event()
    listener = asyncio.create_task(notifier.run(stopping))
    try:
        async with asyncio.timeout(5):
            while not notifier.healthy:
                await asyncio.sleep(0.01)
        yield db, service, task, manager, lease, notifier, schema
    finally:
        stopping.set()
        await notifier.close()
        await asyncio.wait_for(listener, 3)
        async with db.engine.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await db.dispose()


@pytest.mark.postgres
@pytest.mark.parametrize("commit", [False, True])
async def test_notification_is_delivered_only_after_cancel_commit(pg_task, commit):
    db, _, task, manager, lease, notifier, _ = pg_task
    stopping = asyncio.Event()
    heartbeat = asyncio.create_task(
        LeaseHeartbeat(manager, interval_seconds=10, cancellation_notifier=notifier).run(
            lease, stopping
        )
    )
    try:
        await asyncio.sleep(0)
        async with db.session_factory() as session:
            row = await session.get(TaskRecord, task.task.id)
            row.cancel_requested = True
            await session.flush()
            await asyncio.sleep(0.05)
            assert not heartbeat.done()
            if commit:
                await session.commit()
            else:
                await session.rollback()
        if commit:
            with pytest.raises(TaskCancellationRequested):
                await asyncio.wait_for(heartbeat, 1)
        else:
            await asyncio.sleep(0.05)
            assert not heartbeat.done()
    finally:
        stopping.set()
        await asyncio.gather(heartbeat, return_exceptions=True)
    assert not notifier._waiters


@pytest.mark.postgres
async def test_real_cancel_committing_after_merged_snapshot_cannot_wait_another_tick(pg_task):
    db, service, task, _, lease, notifier, _ = pg_task
    calls = []
    committed, release_snapshot = asyncio.Event(), asyncio.Event()

    class RaceManager(JobLeaseManager):
        async def heartbeat_and_status(self, current):
            status = await super().heartbeat_and_status(current)
            assert not status.cancel_requested
            calls.append("captured_false")
            await service.cancel_task(task.task.id)
            committed.set()
            await release_snapshot.wait()
            return status

        async def cancellation_requested(self, current):
            calls.append("racing_cancel_rechecked")
            return await super().cancellation_requested(current)

    manager = RaceManager(db.session_factory, lease_seconds=60)
    running = asyncio.create_task(
        LeaseHeartbeat(manager, interval_seconds=0.01, cancellation_notifier=notifier).run(
            lease, asyncio.Event()
        )
    )
    try:
        # Arrange the durable commit outside the post-commit observation budget.
        # This remains bounded; a hung cancellation transaction still fails.
        await asyncio.wait_for(committed.wait(), 5)
        release_snapshot.set()
        with pytest.raises(TaskCancellationRequested):
            await asyncio.wait_for(running, 1)
    finally:
        release_snapshot.set()
        if not running.done():
            running.cancel()
        await asyncio.gather(running, return_exceptions=True)
    assert calls == ["captured_false", "racing_cancel_rechecked"]


@pytest.mark.postgres
async def test_ordinary_merged_tick_has_one_transaction_and_one_database_clock(pg_task):
    db, _, _, manager, lease, _, _ = pg_task
    counts = {"begin": 0, "commit": 0, "clock": 0, "statements": 0}

    def begin(connection):
        counts["begin"] += 1

    def commit(connection):
        counts["commit"] += 1

    def statement(connection, cursor, sql, params, context, many):
        counts["statements"] += 1
        counts["clock"] += "clock_timestamp" in sql

    event.listen(db.engine.sync_engine, "begin", begin)
    event.listen(db.engine.sync_engine, "commit", commit)
    event.listen(db.engine.sync_engine, "before_cursor_execute", statement)
    try:
        await manager.heartbeat_and_status(lease)
        assert counts == {"begin": 1, "commit": 1, "clock": 1, "statements": 4}
        counts.update(dict.fromkeys(counts, 0))
        renewed = await manager.heartbeat(lease)
        assert not await manager.cancellation_requested(renewed)
        assert counts["begin"] == 2 and counts["clock"] == 3
        assert counts["statements"] == 8
    finally:
        event.remove(db.engine.sync_engine, "begin", begin)
        event.remove(db.engine.sync_engine, "commit", commit)
        event.remove(db.engine.sync_engine, "before_cursor_execute", statement)


@pytest.mark.postgres
async def test_untrusted_hint_is_not_a_cancellation_decision(pg_task):
    db, _, _, manager, lease, notifier, _ = pg_task
    stopping = asyncio.Event()
    heartbeat = asyncio.create_task(
        LeaseHeartbeat(manager, interval_seconds=10, cancellation_notifier=notifier).run(
            lease, stopping
        )
    )
    try:
        await asyncio.sleep(0)
        async with db.engine.begin() as connection:
            await connection.execute(
                text("SELECT pg_notify('evoagent_task_cancel', :task_id)"),
                {"task_id": str(lease.task_id)},
            )
        await asyncio.sleep(0.05)
        assert not heartbeat.done()
    finally:
        stopping.set()
        await asyncio.gather(heartbeat, return_exceptions=True)


@pytest.mark.postgres
async def test_old_schema_missing_trigger_uses_original_post_commit_query(pg_task):
    db, service, task, _, lease, original, schema = pg_task
    await original.close()
    async with db.engine.begin() as connection:
        await connection.execute(text(f'DROP TRIGGER tasks_cancel_committed ON "{schema}".tasks'))
    notifier = CancellationNotifier(
        db.session_factory, server_settings={"search_path": f"{schema},public"}
    )
    stopping = asyncio.Event()
    listener = asyncio.create_task(notifier.run(stopping))
    calls = []

    class FallbackManager(JobLeaseManager):
        async def heartbeat(self, current):
            result = await super().heartbeat(current)
            calls.append("legacy_renewal")
            await service.cancel_task(task.task.id)
            return result

        async def heartbeat_and_status(self, current):
            pytest.fail("merge must remain disabled without committed notification trigger")

        async def cancellation_requested(self, current):
            calls.append("legacy_post_commit_query")
            return await super().cancellation_requested(current)

    try:
        async with asyncio.timeout(3):
            while notifier.unavailable_reason != "notification_trigger_missing":
                await asyncio.sleep(0.01)
        with pytest.raises(TaskCancellationRequested):
            await asyncio.wait_for(
                LeaseHeartbeat(
                    FallbackManager(db.session_factory, lease_seconds=60),
                    interval_seconds=0.01,
                    cancellation_notifier=notifier,
                ).run(lease, asyncio.Event()),
                1,
            )
        assert calls == ["legacy_renewal", "legacy_post_commit_query"]
    finally:
        stopping.set()
        await notifier.close()
        await asyncio.wait_for(listener, 3)


@pytest.mark.postgres
async def test_disconnect_during_merged_return_forces_a_guarded_rescan(pg_task):
    db, service, task, _, lease, notifier, schema = pg_task
    calls = []

    class DisconnectManager(JobLeaseManager):
        async def heartbeat_and_status(self, current):
            result = await super().heartbeat_and_status(current)
            assert not result.cancel_requested
            # Only kill this test's separately tagged notification connection.
            async with db.engine.begin() as connection:
                assert await connection.scalar(
                    text(
                        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                        "WHERE application_name=:name AND pid != pg_backend_pid() "
                        "AND query LIKE 'SELECT EXISTS%'"
                    ),
                    {"name": schema},
                )
            async with asyncio.timeout(1):
                while notifier.healthy:
                    await asyncio.sleep(0)
            await service.cancel_task(task.task.id)
            calls.append("committed_during_disconnect")
            return result

        async def cancellation_requested(self, current):
            calls.append("guarded_rescan")
            return await super().cancellation_requested(current)

    with pytest.raises(TaskCancellationRequested):
        await asyncio.wait_for(
            LeaseHeartbeat(
                DisconnectManager(db.session_factory, lease_seconds=60),
                interval_seconds=0.01,
                cancellation_notifier=notifier,
            ).run(lease, asyncio.Event()),
            1,
        )
    assert calls == ["committed_during_disconnect", "guarded_rescan"]


@pytest.mark.postgres
@pytest.mark.parametrize("batched", [False, True])
@pytest.mark.parametrize("idle_backoff", [False, True])
async def test_committed_cancel_stops_real_runner_stream_and_new_tool_dispatch(
    pg_task, tmp_path, batched, idle_backoff
):
    db, service, task, _, lease, notifier, _ = pg_task
    started, closed = asyncio.Event(), asyncio.Event()

    class BlockedProvider:
        calls = 0

        async def stream(self, request):
            self.calls += 1
            yield ProviderEvent(type=ProviderEventType.TEXT_DELTA, text_delta="offline progress")
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                closed.set()

    class OwnedManager(JobLeaseManager):
        async def claim_next(self, owner):
            return lease

    settings = Settings(
        _env_file=None,
        provider="mock",
        workspace=tmp_path / "workspace",
        runtime_event_batching_enabled=batched,
        runtime_heartbeat_status_merge_enabled=True,
    )
    provider = BlockedProvider()
    runner = PersistentAgentRunner(
        settings=settings,
        session_factory=db.session_factory,
        context_builder=ContextBuilder(),
        provider=provider,
        registry=ToolRegistry(),
    )
    maximum_idle_wait = asyncio.Event()

    class FastForwardWakeup(Wakeup):
        async def wait(self, stopping, seconds):
            if seconds < 8:
                return False
            maximum_idle_wait.set()
            return await super().wait(stopping, seconds)

    wakeup = FastForwardWakeup(None, "runner-maintenance", post_commit_hooks_enabled=True)
    maintenance = MaintenanceWorker(
        db.session_factory, LocalArtifactStore(tmp_path / "maintenance"), lane="background"
    )
    worker = JobWorker(
        worker_id="runner-cancel",
        lease_manager=OwnedManager(db.session_factory, lease_seconds=60),
        handler=runner,
        heartbeat_seconds=10,
        poll_seconds=1,
        heartbeat_status_merge=True,
        cancellation_notifier=notifier,
        maintenance_worker=maintenance,
        maintenance_idle_backoff=idle_backoff,
        wakeup=wakeup,
    )
    running = asyncio.create_task(worker.run_once())
    maintenance_task = asyncio.create_task(worker._maintenance_lane()) if idle_backoff else None
    try:
        await asyncio.wait_for(started.wait(), 5)
        if maintenance_task is not None:
            await asyncio.wait_for(maximum_idle_wait.wait(), 2)
        await service.cancel_task(task.task.id)
        assert await asyncio.wait_for(running, 2)
        assert closed.is_set() and provider.calls == 1
        assert (await service.get_task(task.task.id)).task.status == TaskStatus.CANCELLED
        async with db.session_factory() as session:
            assert not tuple(await session.scalars(select(ToolCallRecord)))
    finally:
        worker.stop()
        if not running.done():
            running.cancel()
        await asyncio.gather(running, return_exceptions=True)
        if maintenance_task is not None:
            await asyncio.wait_for(maintenance_task, 2)
        await wakeup.close()
