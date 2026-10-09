import asyncio
from contextlib import contextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from evoagent.db.base import Base
from evoagent.db.session import Database
from evoagent.tasks.lease import (
    HeartbeatStatus,
    JobLease,
    JobLeaseManager,
    TaskCancellationRequested,
)
from evoagent.tasks.lease_guard import LeaseLostError
from evoagent.tasks.service import TaskService
from evoagent.workers.cancellation import CancellationNotifier
from evoagent.workers.heartbeat import LeaseHeartbeat


@pytest.fixture
async def running_task(tmp_path):
    db = Database(f"sqlite+aiosqlite:///{tmp_path / 'heartbeat.db'}")
    async with db.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    service = TaskService(db.session_factory)
    chat = await service.create_session("heartbeat")
    task = await service.create_task(
        session_id=chat.id, goal="offline", provider="mock", model="mock"
    )
    manager = JobLeaseManager(db.session_factory, lease_seconds=60)
    lease = await manager.claim_next("owner")
    try:
        yield db, service, task, manager, lease
    finally:
        await db.dispose()


async def test_merged_renewal_uses_one_clock_and_returns_guarded_status(running_task, monkeypatch):
    _, service, task, manager, lease = running_task
    from evoagent.tasks import lease_guard

    calls = []
    original = lease_guard.database_now

    async def clock(session):
        calls.append(session)
        return await original(session)

    monkeypatch.setattr(lease_guard, "database_now", clock)
    status = await manager.heartbeat_and_status(lease)
    assert len(calls) == 1 and not status.cancel_requested
    assert status.lease.epoch == lease.epoch and status.lease.owner == lease.owner
    await service.cancel_task(task.task.id)
    calls.clear()
    assert (await manager.heartbeat_and_status(status.lease)).cancel_requested
    assert len(calls) == 1
    foreign = JobLease(lease.task_id, lease.run_id, "foreign", lease.expires_at, lease.attempt)
    with pytest.raises(LeaseLostError):
        await manager.heartbeat_and_status(foreign)


class Signals:
    def __init__(self, healthy=True):
        self.healthy = healthy
        self.signal = asyncio.Event()
        self.registered = False

    @contextmanager
    def register(self, task_id):
        self.registered = True
        try:
            yield self.signal
        finally:
            self.registered = False


class Manager:
    def __init__(self, signals, *, race=False):
        self.signals = signals
        self.race = race
        self.calls = []

    async def heartbeat_and_status(self, lease):
        self.calls.append("merged")
        if self.race:
            # Cancel commits just after this transaction captured false.
            self.signals.signal.set()
        return HeartbeatStatus(lease, not self.race)

    async def heartbeat(self, lease):
        self.calls.append("heartbeat")
        return lease

    async def cancellation_requested(self, lease):
        self.calls.append("cancel_query")
        return True


@pytest.mark.parametrize(
    "healthy,race,expected",
    [
        (True, False, ["merged"]),
        (True, True, ["merged", "cancel_query"]),
        (False, False, ["heartbeat", "cancel_query"]),
    ],
)
async def test_merge_race_and_degraded_fallback(healthy, race, expected):
    signals = Signals(healthy)
    manager = Manager(signals, race=race)
    lease = JobLease(uuid4(), uuid4(), "owner", datetime.now(UTC), 1)
    heartbeat = LeaseHeartbeat(manager, interval_seconds=0.005, cancellation_notifier=signals)
    with pytest.raises(TaskCancellationRequested):
        await asyncio.wait_for(heartbeat.run(lease, asyncio.Event()), 0.5)
    assert manager.calls == expected and not signals.registered


async def test_committed_hint_interrupts_full_interval_but_rechecks_database():
    signals = Signals()
    manager = Manager(signals)
    lease = JobLease(uuid4(), uuid4(), "owner", datetime.now(UTC), 1)
    heartbeat = LeaseHeartbeat(manager, interval_seconds=10, cancellation_notifier=signals)
    task = asyncio.create_task(heartbeat.run(lease, asyncio.Event()))
    await asyncio.sleep(0)
    signals.signal.set()
    with pytest.raises(TaskCancellationRequested):
        await asyncio.wait_for(task, 0.5)
    assert manager.calls == ["cancel_query"] and not signals.registered


async def test_disabled_heartbeat_retains_independent_post_commit_query():
    manager = Manager(Signals())
    lease = JobLease(uuid4(), uuid4(), "owner", datetime.now(UTC), 1)
    with pytest.raises(TaskCancellationRequested):
        await asyncio.wait_for(
            LeaseHeartbeat(manager, interval_seconds=0.005).run(lease, asyncio.Event()), 0.5
        )
    assert manager.calls == ["heartbeat", "cancel_query"]


async def test_notifier_bounds_untrusted_hints_and_close_removes_waiters():
    notifier = CancellationNotifier(SimpleNamespace(kw={}), max_waiters=1)
    task_id = uuid4()
    with notifier.register(task_id) as signal:
        with pytest.raises(ValueError, match="capacity"), notifier.register(uuid4()):
            pass
        notifier._notify(None, 0, "", "not-a-uuid")
        notifier._notify(None, 0, "", str(uuid4()))
        assert not signal.is_set()
        notifier._notify(None, 0, "", str(task_id))
        assert signal.is_set()
        signal.clear()
        notifier._degraded("disconnected")
        assert signal.is_set() and not notifier.healthy
    assert not notifier._waiters
    await notifier.close()
    with pytest.raises(RuntimeError, match="closed"), notifier.register(task_id):
        pass


async def test_stopping_heartbeat_cleans_signal_registration():
    signals = Signals()
    manager = Manager(signals)
    stopping = asyncio.Event()
    lease = JobLease(uuid4(), uuid4(), "owner", datetime.now(UTC), 1)
    task = asyncio.create_task(
        LeaseHeartbeat(manager, interval_seconds=10, cancellation_notifier=signals).run(
            lease, stopping
        )
    )
    await asyncio.sleep(0)
    stopping.set()
    await asyncio.wait_for(task, 0.5)
    assert not signals.registered and not manager.calls
