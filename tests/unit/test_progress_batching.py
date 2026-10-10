import asyncio
import importlib.util
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import select

from evoagent.core.events import InvalidProgressEvent, ProgressEventType
from evoagent.core.models import EventType
from evoagent.db.base import Base
from evoagent.db.models import RunRecord, SessionRecord, TaskRecord
from evoagent.db.repositories.events import RunEventRepository
from evoagent.db.session import Database
from evoagent.db.unit_of_work import UnitOfWork
from evoagent.memory.schema import MemoryError
from evoagent.tasks.lease_guard import LeaseLostError
from evoagent.trace.persistent_sink import PersistentEventSink


@pytest.fixture(params=["sqlite", "postgres"])
async def event_db(tmp_path, request):
    url = f"sqlite+aiosqlite:///{tmp_path / 'events.db'}"
    if request.param == "postgres":
        url = os.getenv("EVOAGENT_TEST_DATABASE_URL")
        if not url:
            pytest.skip("requires isolated PostgreSQL")
    db = Database(url)
    async with db.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with db.session_factory() as session:
        chat = SessionRecord(title="batch contracts")
        session.add(chat)
        await session.flush()
        task = TaskRecord(session_id=chat.id, goal="progress")
        session.add(task)
        await session.flush()
        run = RunRecord(task_id=task.id, provider="mock", model="mock")
        session.add(run)
        await session.commit()
        run_id = run.id
    try:
        yield db, run_id
    finally:
        # This fixture owns the isolated test database. Do not leak metadata
        # tables into a following migration test or another test order.
        async with db.engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)
        await db.dispose()


async def rows(db, run_id):
    async with db.session_factory() as session:
        return await RunEventRepository(session).list_for_run(run_id)


async def test_off_matches_legacy_emit_byte_for_byte(event_db):
    db, run_id = event_db
    spec = importlib.util.spec_from_file_location(
        "pre_p6a_sink", Path(__file__).parents[1] / "fixtures/runtime/reference_sink.py"
    )
    reference = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(reference)
    old = reference.PersistentEventSink(run_id, db.session_factory)
    off = PersistentEventSink(run_id, db.session_factory, batch_progress=False)
    payload = {"text": "password: hunter2", "token_usage": 4}
    expected = await old.emit(EventType.MODEL_DELTA, payload)
    receipt = await off.append_progress(ProgressEventType.MODEL_DELTA, payload)
    actual = off.events[0]
    assert receipt.durability == "committed" and receipt.accepted_watermark == 1
    assert actual.type == expected.type
    assert actual.payload == expected.payload
    assert actual.sequence == expected.sequence + 1
    assert len(await rows(db, run_id)) == 2
    await off.close()


async def test_buffer_has_no_sequence_and_critical_event_is_commit_barrier(event_db):
    db, run_id = event_db
    sink = PersistentEventSink(run_id, db.session_factory, batch_progress=True)
    try:
        receipt = await sink.append_progress(
            ProgressEventType.MODEL_DELTA, {"text": "password: hunter2"}
        )
        assert receipt.durability == "buffered" and not hasattr(receipt, "sequence")
        assert sink.events == () and await rows(db, run_id) == ()
        assert "hunter2" not in str(sink._pending)
        event = await sink.emit(EventType.MODEL_COMPLETED, {"iteration": 1})
        assert event.sequence == 2
        assert [r.sequence for r in await rows(db, run_id)] == [1, 2]
    finally:
        await sink.close()


async def test_timer_flushes_without_another_model_delta(event_db):
    db, run_id = event_db
    sink = PersistentEventSink(run_id, db.session_factory, batch_progress=True)
    try:
        await sink.append_progress(ProgressEventType.MODEL_DELTA, {"text": "first"})
        async with asyncio.timeout(2):
            while not sink.events:
                await asyncio.sleep(0.01)
        assert len(await rows(db, run_id)) == 1
        assert sink._flushed_watermark == 1
    finally:
        await sink.close()


async def test_count_and_byte_bounds_are_enforced(event_db):
    db, run_id = event_db
    sink = PersistentEventSink(run_id, db.session_factory, batch_progress=True)
    try:
        for i in range(32):
            await sink.append_progress(ProgressEventType.MODEL_DELTA, {"i": i})
        assert len(sink.events) == 32 and sink._pending_bytes == 0
        # 每个字符串仍有截断上限，多字段不能越过累计字节限制。
        payload = {str(i): "x" * 2000 for i in range(20)}
        await sink.append_progress(ProgressEventType.MODEL_DELTA, payload)
        await sink.append_progress(ProgressEventType.MODEL_DELTA, payload)
        assert len(sink.events) == 33 and len(sink._pending) == 1
        assert sink._pending_bytes < 64 * 1024
        with pytest.raises(ValueError, match="byte budget"):
            await sink.append_progress(
                ProgressEventType.MODEL_DELTA, {str(i): "x" * 2000 for i in range(40)}
            )
    finally:
        await sink.close()


async def test_progress_api_rejects_critical_types_and_close_rejects_late_writes(event_db):
    db, run_id = event_db
    sink = PersistentEventSink(run_id, db.session_factory, batch_progress=True)
    for value in (EventType.MODEL_DELTA, EventType.RUN_COMPLETED, "model.delta"):
        with pytest.raises(InvalidProgressEvent):
            await sink.append_progress(value, {})
    await sink.close()
    await sink.close()
    with pytest.raises(RuntimeError, match="closed"):
        await sink.append_progress(ProgressEventType.MODEL_DELTA, {})
    with pytest.raises(RuntimeError, match="closed"):
        await sink.emit(EventType.MODEL_COMPLETED, {})


async def test_abort_discards_and_lease_failure_never_replays(event_db):
    db, run_id = event_db
    sink = PersistentEventSink(run_id, db.session_factory, batch_progress=True)
    await sink.append_progress(ProgressEventType.MODEL_DELTA, {"text": "discard"})
    await sink.abort()
    await sink.close()
    assert await rows(db, run_id) == ()

    class FencedGuard:
        class Lease:
            pass

        lease = Lease()

        async def check(self, session):
            raise LeaseLostError("fenced")

    guard = FencedGuard()
    guard.lease.run_id = run_id
    failing = PersistentEventSink(
        run_id, db.session_factory, batch_progress=True, lease_guard=guard
    )
    await failing.append_progress(ProgressEventType.MODEL_DELTA, {})
    with pytest.raises(LeaseLostError):
        await failing.flush()
    await failing.abort()
    await failing.close()
    assert await rows(db, run_id) == ()


async def test_uncertain_commit_does_not_duplicate_batch(event_db, monkeypatch):
    db, run_id = event_db
    commit = UnitOfWork.commit
    attempts = 0

    async def uncertain(unit):
        nonlocal attempts
        attempts += 1
        await commit(unit)
        raise RuntimeError("commit acknowledgement lost")

    monkeypatch.setattr(UnitOfWork, "commit", uncertain)
    sink = PersistentEventSink(run_id, db.session_factory, batch_progress=True)
    await sink.append_progress(ProgressEventType.MODEL_DELTA, {"text": "one"})
    with pytest.raises(RuntimeError, match="acknowledgement"):
        await sink.flush()
    with pytest.raises(RuntimeError, match="acknowledgement"):
        await sink.close()
    assert attempts == 1 and len(await rows(db, run_id)) == 1


async def test_source_is_rechecked_at_commit_not_only_at_buffer_acceptance(event_db, monkeypatch):
    db, run_id = event_db

    class OwnedGuard:
        class Lease:
            pass

        lease = Lease()

        async def check(self, session):
            return None

    async def revoked(session, checked_run_id):
        assert checked_run_id == run_id
        raise MemoryError("source_revoked")

    guard = OwnedGuard()
    guard.lease.run_id = run_id
    sink = PersistentEventSink(run_id, db.session_factory, batch_progress=True, lease_guard=guard)
    await sink.append_progress(ProgressEventType.MODEL_DELTA, {"text": "old source content"})
    monkeypatch.setattr("evoagent.trace.persistent_sink.check_run_references", revoked)
    await sink.close()
    assert (await rows(db, run_id))[0].payload == {"context_source_revoked": True}


async def test_background_failure_stops_sleeping_executor(event_db, monkeypatch):
    db, run_id = event_db
    stopped = asyncio.Event()

    async def broken(unit):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(UnitOfWork, "commit", broken)

    async def execution():
        owner = asyncio.current_task()
        sink = PersistentEventSink(
            run_id,
            db.session_factory,
            batch_progress=True,
            on_background_error=lambda error: owner.cancel(),
        )
        try:
            await sink.append_progress(ProgressEventType.MODEL_DELTA, {})
            await asyncio.sleep(10)
            pytest.fail("execution must stop before another tool/model call")
        except asyncio.CancelledError:
            stopped.set()
        finally:
            with pytest.raises(RuntimeError, match="unavailable"):
                await sink.close()

    task = asyncio.create_task(execution())
    await asyncio.wait_for(task, timeout=2)
    assert stopped.is_set() and await rows(db, run_id) == ()


async def test_batch_sequence_allocation_is_atomic_and_rollback_safe(event_db):
    db, run_id = event_db

    async def append(count):
        async with db.session_factory() as session:
            result = await RunEventRepository(session).append_many(
                run_id,
                [
                    dict(event_type="model.delta", payload={}, created_at=datetime.now(UTC))
                    for _ in range(count)
                ],
            )
            await session.commit()
            return [r.sequence for r in result]

    results = await asyncio.gather(append(3), append(4), append(5))
    assert sorted(n for group in results for n in group) == list(range(1, 13))
    assert all(group == list(range(group[0], group[0] + len(group))) for group in results)
    async with db.session_factory() as session:
        await RunEventRepository(session).append_many(
            run_id, [dict(event_type="model.delta", payload={}, created_at=datetime.now(UTC))]
        )
        await session.rollback()
        assert (
            await session.scalar(
                select(RunRecord.next_event_sequence).where(RunRecord.id == run_id)
            )
            == 13
        )
    assert await append(1) == [13]
