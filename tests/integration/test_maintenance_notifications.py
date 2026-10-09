import asyncio
import os
from uuid import uuid4

import pytest
from redis.asyncio import Redis
from sqlalchemy import delete

from evoagent.db.base import Base
from evoagent.db.models import MaintenanceJobRecord
from evoagent.db.session import Database
from evoagent.workers.wakeup import Wakeup


@pytest.mark.postgres
@pytest.mark.redis
@pytest.mark.parametrize(
    "mode", ["commit", "begin", "rollback", "nested_commit", "nested_rollback"]
)
async def test_committed_queue_notice_between_independent_coordinators(mode):
    database_url = os.getenv("EVOAGENT_TEST_DATABASE_URL")
    redis_url = os.getenv("EVOAGENT_TEST_REDIS_URL")
    if not database_url or not redis_url:
        pytest.skip("requires isolated PostgreSQL and Redis")
    db = Database(database_url)
    async with db.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    first, second = Redis.from_url(redis_url), Redis.from_url(redis_url)
    namespace = "maintenance-contract-" + uuid4().hex
    producer = Wakeup(first, namespace, post_commit_hooks_enabled=True)
    consumer = Wakeup(second, namespace, post_commit_hooks_enabled=True)
    db.session_factory.configure(info={"wakeup": producer})
    listener = asyncio.create_task(consumer.listen())
    job_id = uuid4()
    try:
        # Redis confirms the listener subscription before the only test publish.
        async with asyncio.timeout(2):
            while not (await first.pubsub_numsub(consumer.channel))[0][1]:
                await asyncio.sleep(0.01)
        async with db.session_factory() as session:
            job = MaintenanceJobRecord(
                id=job_id, kind="archive", dedupe_key=str(job_id), payload={}
            )
            if mode == "begin":
                async with session.begin():
                    session.add(job)
                    await session.flush()
                    assert not producer.event.is_set() and not consumer.event.is_set()
            elif mode.startswith("nested"):
                nested = await session.begin_nested()
                session.add(job)
                await session.flush()
                if mode == "nested_commit":
                    await nested.commit()
                else:
                    await nested.rollback()
                await asyncio.sleep(0)
                assert not producer.event.is_set() and not consumer.event.is_set()
                await session.commit()
            else:
                session.add(job)
                await session.flush()
                assert not producer.event.is_set() and not consumer.event.is_set()
                if mode == "rollback":
                    await session.rollback()
                await session.commit()
        if mode in {"commit", "begin", "nested_commit"}:
            await asyncio.wait_for(consumer.event.wait(), timeout=1)
            async with db.session_factory() as session:
                assert await session.get(MaintenanceJobRecord, job_id) is not None
        else:
            await asyncio.sleep(0.05)
            assert not producer.event.is_set() and not consumer.event.is_set()
    finally:
        listener.cancel()
        await asyncio.gather(listener, return_exceptions=True)
        await producer.close()
        await consumer.close()
        async with db.session_factory() as session:
            await session.execute(
                delete(MaintenanceJobRecord).where(MaintenanceJobRecord.id == job_id)
            )
            await session.commit()
        await db.dispose()
        await first.aclose()
        await second.aclose()
