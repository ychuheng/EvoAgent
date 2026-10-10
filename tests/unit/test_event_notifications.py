import asyncio
import importlib.util
import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from redis.asyncio import Redis

from evoagent.config import Settings
from evoagent.db.base import Base
from evoagent.db.models import RunRecord, SessionRecord, TaskRecord
from evoagent.db.repositories.events import RunEventRepository
from evoagent.db.session import Database
from evoagent.tasks.state_machine import PersistentRunStatus
from evoagent.trace.notifications import RunEventNotifier
from evoagent.trace.sse import SseEventService


@pytest.fixture(params=["sqlite", "postgres"])
async def notify_db(tmp_path, request):
    url = f"sqlite+aiosqlite:///{tmp_path / 'notify.db'}"
    if request.param == "postgres":
        url = os.getenv("EVOAGENT_TEST_DATABASE_URL")
        if not url:
            pytest.skip("requires isolated PostgreSQL")
    db = Database(url)
    async with db.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with db.session_factory() as session:
        chat = SessionRecord(title="notifications")
        session.add(chat)
        await session.flush()
        task = TaskRecord(session_id=chat.id, goal="events")
        session.add(task)
        await session.flush()
        run = RunRecord(task_id=task.id, provider="mock", model="mock")
        session.add(run)
        await session.commit()
        run_id = run.id
    notifier = RunEventNotifier(None, "test")
    db.session_factory.configure(info={"event_notifier": notifier})
    yield db, run_id, notifier
    await notifier.close()
    await db.dispose()


async def append(session, run_id, text="event"):
    return await RunEventRepository(session).append(
        run_id=run_id,
        event_type="model.delta",
        payload={"text": text},
        created_at=datetime.now(UTC),
    )


async def test_generation_prevents_query_to_wait_race_and_shares_run_signal():
    notifier = RunEventNotifier(None, "test", capacity=1)
    run_id, second = uuid4(), uuid4()
    async with notifier.subscribe(run_id) as first, notifier.subscribe(run_id) as shared:
        assert first and shared and len(notifier._signals) == 1
        observed = notifier.generation(run_id)
        await notifier.publish(run_id)  # 提示恰好落在 DB 读取完成与 wait 之间。
        await asyncio.wait_for(notifier.wait(run_id, observed, 2), timeout=0.2)
        async with notifier.subscribe(second) as subscribed:
            assert not subscribed and len(notifier._signals) == 1
    assert not notifier._signals
    await notifier.close()


async def test_outer_commit_publishes_direct_session_and_begin_context(notify_db):
    db, run_id, notifier = notify_db
    async with notifier.subscribe(run_id):
        for direct in (True, False):
            observed = notifier.generation(run_id)
            async with db.session_factory() as session:
                if direct:
                    await append(session, run_id)
                    assert notifier.generation(run_id) == observed
                    await session.commit()
                else:
                    async with session.begin():
                        await append(session, run_id)
                        assert notifier.generation(run_id) == observed
            await asyncio.wait_for(notifier.wait(run_id, observed, 2), timeout=0.5)
            assert notifier.generation(run_id) > observed


async def test_rollback_does_not_publish_or_leak_next_transaction(notify_db):
    db, run_id, notifier = notify_db
    async with notifier.subscribe(run_id):
        async with db.session_factory() as session:
            await append(session, run_id)
            await session.rollback()
            await session.commit()  # 空事务不得发布上次 rollback 的提示。
        await asyncio.sleep(0)
        assert notifier.generation(run_id) == 0 and not notifier._pending


async def test_configured_database_owns_and_closes_notification_resources(notify_db):
    db, _, _ = notify_db
    settings = Settings(
        _env_file=None,
        database_url=db.engine.url.render_as_string(hide_password=False),
        runtime_shared_notifications_enabled=True,
        redis_url=None,
    )
    async with Database.configured(settings) as configured:
        notifier = configured.session_factory.kw["info"]["event_notifier"]
        assert not notifier._closed
    assert notifier._closed


async def test_redis_publish_failure_does_not_undo_database_commit(notify_db):
    db, run_id, _ = notify_db

    class BrokenRedis:
        async def publish(self, *args):
            raise OSError("offline")

    notifier = RunEventNotifier(BrokenRedis(), "test")
    db.session_factory.configure(info={"event_notifier": notifier})
    try:
        async with notifier.subscribe(run_id):
            async with db.session_factory() as session:
                await append(session, run_id)
                await session.commit()
            await asyncio.wait_for(notifier.wait(run_id, 0, 2), timeout=0.5)
        await notifier._publisher
        async with db.session_factory() as session:
            assert len(await RunEventRepository(session).list_for_run(run_id)) == 1
    finally:
        await notifier.close()


async def test_savepoint_commit_is_not_outer_commit_and_rollback_discards(notify_db):
    db, run_id, notifier = notify_db
    async with notifier.subscribe(run_id):
        async with db.session_factory() as session:
            async with session.begin_nested():
                await append(session, run_id)
            await asyncio.sleep(0)
            assert notifier.generation(run_id) == 0
            await session.rollback()
            await session.commit()
        await asyncio.sleep(0)
        assert notifier.generation(run_id) == 0
        async with db.session_factory() as session:
            async with session.begin(), session.begin_nested():
                await append(session, run_id)
            await asyncio.wait_for(notifier.wait(run_id, 0, 2), timeout=0.5)


async def test_uncommitted_events_are_invisible_and_commit_wakes_sse(notify_db):
    db, run_id, notifier = notify_db
    stream = SseEventService(
        db.session_factory, poll_seconds=2, heartbeat_seconds=10, notifier=notifier
    ).stream(run_id)
    read = asyncio.create_task(anext(stream))
    try:
        async with db.session_factory() as session:
            await append(session, run_id, "committed only")
            await asyncio.sleep(0.05)
            assert not read.done()
            await session.commit()
        result = await asyncio.wait_for(read, timeout=1)
        assert result.id == "1" and result.event == "model.delta"
        assert result.data["payload"]["text"] == "committed only"
    finally:
        read.cancel()
        await asyncio.gather(read, return_exceptions=True)
        await stream.aclose()
    assert not notifier._signals


async def test_backlog_is_paged_without_sleep_and_matches_off_projection(notify_db, monkeypatch):
    db, run_id, notifier = notify_db
    async with db.session_factory() as session:
        await RunEventRepository(session).append_many(
            run_id,
            [
                dict(event_type="model.delta", payload={"index": i}, created_at=datetime.now(UTC))
                for i in range(451)
            ],
        )
        run = await session.get(RunRecord, run_id)
        run.status = PersistentRunStatus.COMPLETED
        await session.commit()
    sizes = []
    page = RunEventRepository.page_for_run

    async def measured(repo, *args, **kwargs):
        result = await page(repo, *args, **kwargs)
        sizes.append(len(result))
        return result

    monkeypatch.setattr(RunEventRepository, "page_for_run", measured)

    async def unexpected_wait(*args, **kwargs):
        raise AssertionError("terminal backlog must drain without waiting for notification")

    monkeypatch.setattr(notifier, "wait", unexpected_wait)
    on = SseEventService(
        db.session_factory, poll_seconds=5, heartbeat_seconds=10, notifier=notifier
    )
    # Prove the no-wait contract directly; database round-trip time on a shared
    # CI runner is not a notification latency benchmark. Keep a deadlock bound.
    async with asyncio.timeout(10):
        result = [event async for event in on.stream(run_id, after_sequence=1)]
    assert sizes == [200, 200, 50, 0]
    assert [e.id for e in result] == [str(n) for n in range(2, 452)]
    off = SseEventService(db.session_factory, poll_seconds=0.001, heartbeat_seconds=10)
    reference = [event async for event in off.stream(run_id, after_sequence=1)]
    assert [e.model_dump() for e in result] == [e.model_dump() for e in reference]
    spec = importlib.util.spec_from_file_location(
        "pre_p6a_sse", Path(__file__).parents[1] / "fixtures/runtime/reference_sse.py"
    )
    oracle = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(oracle)
    old = oracle.SseEventService(db.session_factory, poll_seconds=0.001, heartbeat_seconds=10)
    original = [event async for event in old.stream(run_id, after_sequence=1)]
    assert [e.model_dump() for e in reference] == [e.model_dump() for e in original]


async def test_lost_notification_falls_back_to_database(notify_db):
    db, run_id, notifier = notify_db
    # producer 故意未装配提示 helper，模拟提示丢失。
    db.session_factory.configure(info={})
    stream = SseEventService(
        db.session_factory, poll_seconds=0.001, heartbeat_seconds=10, notifier=notifier
    ).stream(run_id)
    read = asyncio.create_task(anext(stream))
    try:
        await asyncio.sleep(0.05)
        async with db.session_factory() as session:
            await append(session, run_id)
            await session.commit()
        result = await asyncio.wait_for(read, timeout=3)
        assert result.id == "1"
    finally:
        read.cancel()
        await asyncio.gather(read, return_exceptions=True)
        await stream.aclose()


async def test_publisher_backlog_is_bounded_and_shutdown_cleans_tasks():
    notifier = RunEventNotifier(None, "test", capacity=2)
    notifier.schedule_publish({uuid4() for _ in range(10)})
    assert len(notifier._pending) == 2 and notifier.dropped_hints == 8
    await notifier.close()
    assert not notifier._pending and notifier._publisher.done()


async def test_redis_fans_committed_hint_out_across_process_coordinators():
    url = os.getenv("EVOAGENT_TEST_REDIS_URL")
    if not url:
        pytest.skip("requires isolated Redis")
    client = Redis.from_url(url)
    namespace = "run-notify-test-" + uuid4().hex
    reader = RunEventNotifier(client, namespace)
    writer = RunEventNotifier(client, namespace)
    run_id = uuid4()
    try:
        await reader.start()
        async with reader.subscribe(run_id):
            observed = reader.generation(run_id)
            await writer.publish(run_id)
            await asyncio.wait_for(reader.wait(run_id, observed, 2), timeout=1)
            assert reader.generation(run_id) > observed
    finally:
        await reader.close()
        await writer.close()
        await client.aclose()
