import asyncio

import pytest
from sqlalchemy import select

from evoagent.db.models import LearningRequestRecord, MaintenanceJobRecord
from evoagent.learning.service import LearningService
from tests.integration.test_validation_admission import inputs

pytest_plugins = ("tests.integration.test_personal_trials",)


async def test_start_is_explicit_durable_and_replay_never_resurrects_cancellation(trial_candidate):
    service, parent, payload, _ = await inputs(trial_candidate)
    request = await service.prepare_cases(parent.id, payload)
    _, db, *_ = trial_candidate
    async with db.session_factory() as session:
        assert not list(
            await session.scalars(
                select(MaintenanceJobRecord).where(
                    MaintenanceJobRecord.learning_request_id == request.id,
                )
            )
        )
    first = await service.start(request.id, request.lock_version)
    assert first == await service.start(request.id, request.lock_version)
    async with db.session_factory() as session:
        jobs = list(
            await session.scalars(
                select(MaintenanceJobRecord).where(
                    MaintenanceJobRecord.learning_request_id == request.id,
                )
            )
        )
        assert len(jobs) == 1 and jobs[0].status == "pending"
    await LearningService(db.session_factory).cancel_request(request.id, request.lock_version)
    replay = await service.start(request.id, request.lock_version)
    assert replay.status == "cancelled"
    async with db.session_factory() as session:
        jobs = list(
            await session.scalars(
                select(MaintenanceJobRecord).where(
                    MaintenanceJobRecord.learning_request_id == request.id,
                )
            )
        )
        assert len(jobs) == 1 and jobs[0].status == "cancelled"


async def test_concurrent_postgres_start_creates_one_job(trial_candidate):
    service, parent, payload, _ = await inputs(trial_candidate)
    request = await service.prepare_cases(parent.id, payload)
    _, db, *_ = trial_candidate
    if db.engine.dialect.name != "postgresql":
        pytest.skip("requires PostgreSQL row serialization")
    replies = await asyncio.gather(
        service.start(request.id, request.lock_version),
        service.start(request.id, request.lock_version),
    )
    assert replies[0].id == replies[1].id == request.id
    async with db.session_factory() as session:
        jobs = list(
            await session.scalars(
                select(MaintenanceJobRecord).where(
                    MaintenanceJobRecord.learning_request_id == request.id,
                )
            )
        )
        assert len(jobs) == 1
        row = await session.get(LearningRequestRecord, request.id)
        assert row.stage == "task_validate" and row.status == "queued"
