"""Automatic suggestions are opt-in candidates, never adoption evidence."""

import asyncio

from sqlalchemy import select

from evoagent.db.models import (
    DEFAULT_WORKSPACE_ID,
    LearningPolicyRecord,
    LearningRequestRecord,
    MaintenanceJobRecord,
    RunRecord,
    TaskRecord,
)
from evoagent.db.repositories.events import RunEventRepository
from evoagent.learning.configuration import candidate_configuration
from evoagent.learning.discovery import CandidateDiscoveryService
from evoagent.learning.service import LearningService
from evoagent.tasks.lease_guard import database_now
from tests.integration.test_learning_api import enable
from tests.integration.test_learning_worker import worker

pytest_plugins = ("tests.integration.test_learning_api",)


def scanner(context):
    _, db, _, settings = context
    return CandidateDiscoveryService(
        LearningService(
            db.session_factory,
            learning_enabled=True,
            generator_configuration=candidate_configuration(settings),
        )
    )


async def evidence(context, run_id=None, passed=True):
    _, db, original, _ = context
    async with db.session_factory() as session:
        await RunEventRepository(session).append(
            run_id=run_id or original,
            event_type="acceptance.checked",
            payload={"passed": passed, "criterion_id": "fixture-output"},
            created_at=await database_now(session),
        )
        await session.commit()


async def suggest(context, *, daily=3, cooldown=0):
    client, db, _, _ = context
    await enable(client)
    async with db.session_factory() as session:
        policy = await session.get(LearningPolicyRecord, DEFAULT_WORKSPACE_ID)
        policy.mode = "suggest"
        policy.daily_candidate_limit = daily
        policy.cooldown_seconds = cooldown
        await session.commit()


async def test_auto_suggest_requires_policy_and_actual_acceptance(learning_api):
    client, db, run_id, _ = learning_api
    discovery = scanner(learning_api)
    assert await discovery.discover_candidates() == 0
    await enable(client)
    await evidence(learning_api)
    assert await discovery.discover_candidates() == 0
    async with db.session_factory() as session:
        (await session.get(LearningPolicyRecord, DEFAULT_WORKSPACE_ID)).mode = "suggest"
        await session.commit()
    assert await discovery.discover_candidates() == 1
    assert await discovery.discover_candidates() == 0
    async with db.session_factory() as session:
        requests = list(await session.scalars(select(LearningRequestRecord)))
        assert len(requests) == 1 and requests[0].trigger == "discover"
        assert requests[0].candidate_version_id is None and requests[0].status == "queued"
        assert requests[0].policy_snapshot["discovery_fingerprint"]
        jobs = list(await session.scalars(select(MaintenanceJobRecord)))
        assert len(jobs) == 1 and jobs[0].kind == "learning_propose"
        assert (await session.get(RunRecord, run_id)).status == "completed"


async def test_no_business_evidence_and_explicit_fact_feedback_do_not_autolearn(learning_api):
    client, _, run_id, _ = learning_api
    await suggest(learning_api)
    discovery = scanner(learning_api)
    assert await discovery.discover_candidates() == 0
    await evidence(learning_api, passed=False)
    assert await discovery.discover_candidates() == 0
    await evidence(learning_api)
    response = await client.post(
        f"/api/v1/runs/{run_id}/feedback",
        json={
            "client_request_id": "fact-only",
            "intent": "fact",
            "verdict": "helpful",
            "learn_from_feedback": False,
        },
    )
    assert response.status_code == 201
    assert await scanner(learning_api).discover_candidates() == 0


async def add_run(context, goal):
    _, db, run_id, _ = context
    async with db.session_factory() as session:
        original = await session.get(RunRecord, run_id)
        task = await session.get(TaskRecord, original.task_id)
        new_task = TaskRecord(
            session_id=task.session_id, goal=goal, status="completed", family=task.family
        )
        session.add(new_task)
        await session.flush()
        run = RunRecord(
            task_id=new_task.id,
            provider="mock",
            model="mock",
            status="completed",
            data_role="personal",
        )
        session.add(run)
        await session.commit()
    await evidence(context, run.id)
    return run.id


async def test_daily_limit_serializes_two_scanners(learning_api):
    await suggest(learning_api, daily=1)
    await evidence(learning_api)
    await add_run(learning_api, "another independent task")
    results = await asyncio.gather(
        scanner(learning_api).discover_candidates(), scanner(learning_api).discover_candidates()
    )
    assert sum(results) == 1
    async with learning_api[1].session_factory() as session:
        assert len(list(await session.scalars(select(LearningRequestRecord)))) == 1


async def test_exact_duplicate_is_not_another_paid_candidate(learning_api):
    await suggest(learning_api)
    await evidence(learning_api)
    await add_run(learning_api, "preserve leading zeros")
    assert await scanner(learning_api).discover_candidates() == 1


async def test_family_cooldown_and_late_acceptance_are_rechecked(learning_api):
    await suggest(learning_api, cooldown=86400)
    discovery = scanner(learning_api)
    assert await discovery.discover_candidates() == 0
    # A late event is visible on the next cyclic pass, never lost behind time.
    await evidence(learning_api)
    await discovery.discover_candidates()  # may reach end and reset its cursor
    assert await discovery.discover_candidates() in {0, 1}
    await add_run(learning_api, "different goal in the same family")
    await scanner(learning_api).discover_candidates()
    async with learning_api[1].session_factory() as session:
        assert len(list(await session.scalars(select(LearningRequestRecord)))) == 1


async def test_switch_to_manual_stops_automatic_candidate_before_generation(learning_api):
    client, db, _, settings = learning_api
    await suggest(learning_api)
    await evidence(learning_api)
    assert await scanner(learning_api).discover_candidates() == 1
    runtime = worker(db, settings)
    assert await runtime.run_once()
    async with db.session_factory() as session:
        (await session.get(LearningPolicyRecord, DEFAULT_WORKSPACE_ID)).mode = "manual"
        await session.commit()
    assert await runtime.run_once()
    async with db.session_factory() as session:
        request = await session.scalar(select(LearningRequestRecord))
        assert request.status == "waiting_disabled"
        assert request.error_code == "learning_discovery_not_authorized"
        assert request.candidate_version_id is None
    response = await client.post(
        f"/api/v1/learning-requests/{request.id}/retry",
        json={
            "expected_lock_version": request.lock_version,
            "client_request_id": "manual-cannot-retry-auto",
        },
    )
    assert response.status_code == 422
