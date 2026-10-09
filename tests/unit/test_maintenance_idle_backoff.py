import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from evoagent.db.base import Base
from evoagent.db.models import (
    MaintenanceJobRecord,
    MemoryEntryRecord,
    MemoryVersionRecord,
    WorkspaceRecord,
)
from evoagent.db.session import Database
from evoagent.memory.maintenance import MaintenanceWorker
from evoagent.tasks.lease import JobLease
from evoagent.trace.artifacts import LocalArtifactStore
from evoagent.workers.main import JobWorker
from evoagent.workers.maintenance import MaintenanceLane
from evoagent.workers.wakeup import Wakeup
from tests.fixtures.runtime.reference_maintenance import MaintenanceWorker as ReferenceMaintenance
from tests.fixtures.runtime.reference_wakeup import Wakeup as ReferenceWakeup
from tests.fixtures.runtime.reference_worker import JobWorker as ReferenceWorker


class VirtualBus:
    def __init__(self, stopping, *, notify_at=None):
        self.stopping = stopping
        self.notify_at = notify_at
        self.elapsed = 0
        self.delays = []

    async def wait(self, stopping, seconds):
        self.delays.append(seconds)
        self.elapsed += seconds
        if self.elapsed >= 60:
            stopping.set()
        await asyncio.sleep(0)
        return len(self.delays) == self.notify_at


class EmptyWorker:
    def __init__(self):
        self.calls = 0

    async def run_once(self):
        self.calls += 1
        return False


async def test_low_priority_idle_budget_and_fixed_poll_reference():
    for enabled, expected in ((False, 60), (True, 10)):
        stopping = asyncio.Event()
        bus = VirtualBus(stopping)
        worker = EmptyWorker()
        await MaintenanceLane(worker, bus, poll_seconds=1, idle_backoff=enabled).run(stopping)
        assert worker.calls == expected
        if enabled:
            assert bus.delays[:5] == [1, 2, 4, 8, 8]
            assert worker.calls <= 12
        else:
            assert set(bus.delays) == {1}


async def test_notification_resets_backoff_even_if_another_worker_claimed_job():
    stopping = asyncio.Event()
    bus = VirtualBus(stopping, notify_at=4)
    await MaintenanceLane(EmptyWorker(), bus, poll_seconds=1, idle_backoff=True).run(stopping)
    assert bus.delays[:5] == [1, 2, 4, 8, 1]


async def test_busy_background_does_not_delay_independent_cleanup_lane():
    stopping = asyncio.Event()
    blocked = asyncio.Event()
    started = asyncio.Event()

    class Background:
        async def run_once(self):
            started.set()
            await blocked.wait()
            return True

    class Cleanup:
        async def run_once(self):
            await started.wait()
            stopping.set()
            return True

    wakeup = Wakeup(None, "test", post_commit_hooks_enabled=True)
    background = asyncio.create_task(
        MaintenanceLane(Background(), wakeup, poll_seconds=1, idle_backoff=True).run(stopping)
    )
    critical = asyncio.create_task(
        MaintenanceLane(Cleanup(), wakeup, poll_seconds=1, drain_immediately=True).run(stopping)
    )
    try:
        await asyncio.wait_for(critical, timeout=0.2)
        assert not background.done()
    finally:
        blocked.set()
        await background
        await wakeup.close()


@pytest.mark.parametrize("stop", [False, True])
async def test_notice_or_shutdown_interrupts_eight_second_idle_wait(stop):
    stopping = asyncio.Event()
    large_wait = asyncio.Event()

    class FastForward(Wakeup):
        async def wait(self, stopping, seconds):
            if seconds < 8:
                return False
            large_wait.set()
            return await super().wait(stopping, seconds)

    bus = FastForward(None, "test", post_commit_hooks_enabled=True)
    worker = EmptyWorker()
    running = asyncio.create_task(
        MaintenanceLane(worker, bus, poll_seconds=1, idle_backoff=True).run(stopping)
    )
    await large_wait.wait()
    calls = worker.calls
    if stop:
        stopping.set()
    else:
        bus.event.set()
        async with asyncio.timeout(0.2):
            while worker.calls == calls:
                await asyncio.sleep(0)
        stopping.set()
    await asyncio.wait_for(running, timeout=0.2)
    await bus.close()


@pytest.mark.parametrize("mode", ["commit", "begin", "rollback", "savepoint"])
async def test_queue_hint_observes_outer_commit_only(tmp_path, mode):
    db = Database(f"sqlite+aiosqlite:///{tmp_path / 'queue.db'}")
    bus = Wakeup(None, "queue", post_commit_hooks_enabled=True)
    db.session_factory.configure(info={"wakeup": bus})
    async with db.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        async with db.session_factory() as session:
            job = MaintenanceJobRecord(kind="archive", dedupe_key="single", payload={})
            if mode == "begin":
                async with session.begin():
                    session.add(job)
                    await session.flush()
                    assert not bus.event.is_set()
            elif mode == "savepoint":
                async with session.begin_nested():
                    session.add(job)
                    await session.flush()
                await asyncio.sleep(0)
                assert not bus.event.is_set()
                await session.rollback()
                await session.commit()
            else:
                session.add(job)
                await session.flush()
                assert not bus.event.is_set()
                if mode == "rollback":
                    await session.rollback()
                    await session.commit()
                else:
                    await session.commit()
        if mode in {"commit", "begin"}:
            await asyncio.wait_for(bus.event.wait(), timeout=0.2)
        else:
            await asyncio.sleep(0)
            assert not bus.event.is_set()
    finally:
        await bus.close()
        await db.dispose()


async def test_lane_claims_do_not_steal_cleanup_and_all_mode_is_compatible(tmp_path):
    db = Database(f"sqlite+aiosqlite:///{tmp_path / 'lanes.db'}")
    async with db.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        async with db.session_factory() as session:
            archive = MaintenanceJobRecord(kind="archive", dedupe_key="older", payload={})
            erase = MaintenanceJobRecord(kind="erase", dedupe_key="newer", payload={})
            session.add_all([archive, erase])
            await session.commit()
            archive_id, erase_id = archive.id, erase.id
        store = LocalArtifactStore(tmp_path / "artifacts")
        background = MaintenanceWorker(db.session_factory, store, lane="background")
        critical = MaintenanceWorker(db.session_factory, store, lane="critical")
        assert (await background.claim())[0] == archive_id
        assert (await critical.claim())[0] == erase_id
        assert await background.claim() is None and await critical.claim() is None
        # 默认 all 的业务筛选合同保持：任一合法旧 kind 均可领取。
        async with db.session_factory() as session:
            index = MaintenanceJobRecord(kind="index_source", dedupe_key="index", payload={})
            session.add(index)
            await session.commit()
            index_id = index.id
        assert (await MaintenanceWorker(db.session_factory, store, SimpleNamespace()).claim())[
            0
        ] == index_id
    finally:
        await db.dispose()


async def test_disabled_claim_matches_frozen_reference_for_due_and_exhausted_jobs(
    tmp_path, monkeypatch
):
    now = datetime(2026, 10, 9, tzinfo=UTC)

    async def clock(session):
        return now

    monkeypatch.setattr("evoagent.memory.maintenance.database_now", clock)
    monkeypatch.setattr("tests.fixtures.runtime.reference_maintenance.database_now", clock)
    projections = []
    for implementation in (ReferenceMaintenance, MaintenanceWorker):
        db = Database(
            f"sqlite+aiosqlite:///{tmp_path / implementation.__module__.split('.')[-1]}.db"
        )
        async with db.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        try:
            async with db.session_factory() as session:
                jobs = [
                    MaintenanceJobRecord(kind="archive", dedupe_key="pending", payload={}),
                    MaintenanceJobRecord(
                        kind="erase",
                        dedupe_key="due",
                        payload={},
                        status="failed",
                        attempts=1,
                        next_attempt_at=now - timedelta(seconds=1),
                    ),
                    MaintenanceJobRecord(
                        kind="archive",
                        dedupe_key="expired",
                        payload={},
                        status="running",
                        attempts=1,
                        lease_expires_at=now - timedelta(seconds=1),
                    ),
                    MaintenanceJobRecord(
                        kind="erase",
                        dedupe_key="future",
                        payload={},
                        status="failed",
                        attempts=1,
                        next_attempt_at=now + timedelta(seconds=10),
                    ),
                    MaintenanceJobRecord(
                        kind="archive",
                        dedupe_key="exhausted",
                        payload={},
                        status="running",
                        attempts=3,
                        lease_expires_at=now - timedelta(seconds=1),
                    ),
                ]
                for i, job in enumerate(jobs):
                    job.created_at = now + timedelta(microseconds=i)
                session.add_all(jobs)
                await session.commit()
                ids = [j.id for j in jobs]
            worker = implementation(db.session_factory, LocalArtifactStore(tmp_path / "store"))
            worker.owner = "frozen-owner"
            claims = [await worker.claim() for _ in range(5)]
            assert [ids.index(c[0]) if c else None for c in claims] == [0, 1, 2, None, None]
            async with db.session_factory() as session:
                projections.append(
                    [
                        (
                            row.status,
                            row.attempts,
                            row.lease_epoch,
                            row.lease_owner,
                            row.error_code,
                            row.next_attempt_at,
                            row.lease_expires_at,
                        )
                        for row in [await session.get(MaintenanceJobRecord, key) for key in ids]
                    ]
                )
        finally:
            await db.dispose()
    assert projections[0] == projections[1]


async def test_disabled_wakeup_matches_frozen_publish_and_wait_semantics():
    class RedisRecorder:
        def __init__(self):
            self.messages = []

        async def publish(self, channel, message):
            self.messages.append((channel, message))

    outcomes = []
    for implementation in (ReferenceWakeup, Wakeup):
        redis = RedisRecorder()
        bus = implementation(redis, "fixed")
        await bus.publish()
        before = bus.event.is_set()
        bus.event.set()
        await bus.wait(asyncio.Event(), 0.1)
        outcomes.append((redis.messages, before, bus.event.is_set()))
    assert outcomes[0] == outcomes[1] == ([("fixed:queue-wakeup", "scan")], False, False)


async def test_real_erasure_completes_while_background_execution_is_blocked(tmp_path):
    db = Database(f"sqlite+aiosqlite:///{tmp_path / 'busy-cleanup.db'}")
    async with db.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    blocked, started, stopping = asyncio.Event(), asyncio.Event(), asyncio.Event()
    store = LocalArtifactStore(tmp_path / "artifacts")
    bus = Wakeup(None, "cleanup", post_commit_hooks_enabled=True)

    class BusyMaintenance(MaintenanceWorker):
        async def execute(self, job_id, epoch):
            started.set()
            await blocked.wait()

    background = BusyMaintenance(db.session_factory, store, lane="background")
    critical = MaintenanceWorker(db.session_factory, store, lane="critical")
    running = None
    try:
        async with db.session_factory() as session:
            workspace = WorkspaceRecord(name="cleanup")
            session.add(workspace)
            await session.flush()
            entry = MemoryEntryRecord(
                workspace_id=workspace.id, scope_key="workspace", fact_key="obsolete"
            )
            session.add(entry)
            await session.flush()
            version = MemoryVersionRecord(
                entry_id=entry.id,
                revision=1,
                kind="fact",
                content="obsolete fact",
                content_hash="0" * 64,
                status="revoked",
                confidence_method="user",
                origin_type="personal",
            )
            session.add(version)
            await session.flush()
            archive = MaintenanceJobRecord(kind="archive", dedupe_key="blocked", payload={})
            session.add(archive)
            await session.commit()
            version_id = version.id
        running = asyncio.create_task(
            MaintenanceLane(background, bus, poll_seconds=1, idle_backoff=True).run(stopping)
        )
        await asyncio.wait_for(started.wait(), 2)
        async with db.session_factory() as session:
            cleanup = MaintenanceJobRecord(
                kind="erase", dedupe_key="cleanup", payload={"version_id": str(version_id)}
            )
            session.add(cleanup)
            await session.commit()
            cleanup_id = cleanup.id
        assert await asyncio.wait_for(critical.run_once(), 5)
        assert not running.done()
        async with db.session_factory() as session:
            assert (await session.get(MaintenanceJobRecord, cleanup_id)).status == "completed"
            version = await session.get(MemoryVersionRecord, version_id)
            assert version.status == "erased" and version.content is None
    finally:
        stopping.set()
        blocked.set()
        if running is not None:
            await asyncio.gather(running, return_exceptions=True)
        await bus.close()
        await db.dispose()


async def test_o5_keeps_active_cancellation_schedule_and_heartbeat_query_gap():
    traces = []

    async def execute(enabled):
        calls = []
        lease = JobLease(uuid4(), uuid4(), "test", datetime.now(UTC), 1)

        class Manager:
            async def promote_due_retries(self):
                pass

            async def recover_expired(self):
                pass

            async def recover_pending(self, **kwargs):
                pass

            async def claim_next(self, owner):
                return lease

            async def heartbeat(self, current):
                calls.append("heartbeat")
                # 取消恰好在续租完成与独立状态查询之间落库。
                self.cancelled = True
                return current

            async def cancellation_requested(self, current):
                calls.append("cancel_query")
                return self.cancelled

            async def finalize(self, current, result):
                calls.append(("finalized", result.status.value, result.error_code))

        class Handler:
            async def handle(self, current):
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    calls.append("dispatch_stopped")
                    raise

        implementation = ReferenceWorker if enabled is None else JobWorker
        worker = implementation(
            worker_id="test",
            lease_manager=Manager(),
            handler=Handler(),
            heartbeat_seconds=0.005,
            poll_seconds=8,
            **({"maintenance_idle_backoff": enabled} if enabled is not None else {}),
        )
        # 空闲维护的最大退避不参与此活动任务的心跳调度。
        assert await asyncio.wait_for(worker.run_once(), 0.5)
        return calls

    for enabled in (None, False, True):
        traces.append(await execute(enabled))

    assert (
        traces[0]
        == traces[1]
        == traces[2]
        == [
            "heartbeat",
            "cancel_query",
            "dispatch_stopped",
            ("finalized", "cancelled", "cancel_requested"),
        ]
    )
