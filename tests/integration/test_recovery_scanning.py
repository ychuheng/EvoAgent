import asyncio
import os

import pytest
from sqlalchemy import func, select

from evoagent.db.base import Base
from evoagent.db.models import RunEventRecord, RunRecord, SessionRecord, TaskRecord
from evoagent.db.session import Database
from evoagent.runtime.recovery import RecoveryService
from evoagent.tasks.lease import JobLeaseManager
from evoagent.tasks.state_machine import PersistentRunStatus, TaskStatus
from evoagent.workers.recovery import RecoveryScanner


@pytest.fixture(params=["sqlite", "postgres"])
async def recovering_db(tmp_path, request):
    url = f"sqlite+aiosqlite:///{tmp_path / 'recovery.db'}"
    if request.param == "postgres":
        url = os.getenv("EVOAGENT_TEST_DATABASE_URL")
        if not url:
            pytest.skip("requires isolated PostgreSQL")
    db = Database(url)
    async with db.engine.begin() as connection:
        if request.param == "postgres":
            await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)
    async with db.session_factory() as session:
        chat = SessionRecord(title="bounded recovery")
        session.add(chat)
        await session.flush()
        tasks = [
            TaskRecord(session_id=chat.id, goal="safe restart", status=TaskStatus.RECOVERING)
            for _ in range(60)
        ]
        session.add_all(tasks)
        await session.flush()
        runs = [
            RunRecord(
                task_id=t.id, provider="mock", model="mock", status=PersistentRunStatus.RECOVERING
            )
            for t in tasks
        ]
        session.add_all(runs)
        await session.commit()
        ids = [t.id for t in tasks]
        run_ids = [r.id for r in runs]
    yield db, ids, run_ids
    await db.dispose()


async def test_pending_scan_obeys_limit_and_preserves_recovery_decisions(recovering_db):
    db, ids, run_ids = recovering_db
    service = RecoveryService(db.session_factory, snapshot_schema_version=1)
    assert await service.recover_pending(limit=50, skip_locked=True) == 50
    async with db.session_factory() as session:
        queued = await session.scalar(
            select(func.count())
            .select_from(TaskRecord)
            .where(TaskRecord.id.in_(ids), TaskRecord.status == TaskStatus.QUEUED)
        )
        assert queued == 50
        decisions = tuple(
            await session.scalars(
                select(RunEventRecord).where(
                    RunEventRecord.run_id.in_(run_ids),
                    RunEventRecord.event_type == "recovery.decided",
                )
            )
        )
        assert len(decisions) == 50 and all(d.payload["action"] == "restart" for d in decisions)
    assert await service.recover_pending(limit=50, skip_locked=True) >= 10


async def test_locked_old_rows_do_not_starve_younger_recoverable_rows(recovering_db):
    db, ids, _ = recovering_db
    if db.engine.dialect.name != "postgresql":
        pytest.skip("SQLite does not provide SKIP LOCKED")
    service = RecoveryService(db.session_factory, snapshot_schema_version=1)
    async with db.session_factory() as locked:
        await locked.execute(
            select(TaskRecord).where(TaskRecord.id.in_(ids[:50])).with_for_update()
        )
        async with asyncio.timeout(2):
            assert await service.recover_pending(limit=50, skip_locked=True) >= 10
        await locked.rollback()
    assert await service.recover_pending(limit=50, skip_locked=True) == 50


async def test_two_scanners_do_not_duplicate_decisions(recovering_db):
    db, ids, run_ids = recovering_db
    if db.engine.dialect.name != "postgresql":
        pytest.skip("requires PostgreSQL row locks")
    scanners = [
        RecoveryScanner(JobLeaseManager(db.session_factory, lease_seconds=30), schema_version=1)
        for _ in range(2)
    ]
    await asyncio.gather(*(scanner.scan_once() for scanner in scanners))
    await asyncio.gather(*(scanner.scan_once() for scanner in scanners))
    async with db.session_factory() as session:
        assert (
            await session.scalar(
                select(func.count())
                .select_from(TaskRecord)
                .where(TaskRecord.id.in_(ids), TaskRecord.status == TaskStatus.QUEUED)
            )
            == 60
        )
        assert (
            await session.scalar(
                select(func.count())
                .select_from(RunEventRecord)
                .where(
                    RunEventRecord.run_id.in_(run_ids),
                    RunEventRecord.event_type == "recovery.decided",
                )
            )
            == 60
        )
