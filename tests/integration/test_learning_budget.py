from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy import select

from evoagent.core.models import TokenUsage
from evoagent.db.models import LearningSpendReservationRecord, MaintenanceJobRecord, SpendRecord
from evoagent.learning.budget import LearningBudgetService
from evoagent.learning.jobs import LearningJobGuard
from evoagent.learning.schema import LearningError
from tests.integration.test_learning_worker import worker

pytest_plugins = ("tests.integration.test_learning_api",)


@pytest.fixture
async def reserved_context(learning_api):
    client, db, run_id, settings = learning_api
    from evoagent.db.models import DEFAULT_WORKSPACE_ID

    approved = await client.put(
        f"/api/v1/workspaces/{DEFAULT_WORKSPACE_ID}/learning-policy",
        json={
            "mode": "manual",
            "expected_lock_version": 0,
            "daily_limit_micros": 20000,
            "request_limit_micros": 10000,
        },
    )
    assert approved.status_code == 200
    response = await client.post(
        f"/api/v1/runs/{run_id}/learning-requests", json={"client_request_id": "budget"}
    )
    assert response.status_code == 202
    request_id = UUID(response.json()["id"])
    runner = worker(db, settings)
    assert await runner.run_once()
    job_id, epoch = await runner.claim()
    guard = LearningJobGuard(job_id, runner.owner, epoch)
    settings = settings.model_copy(
        update={
            "budget_scope": "trial",
            "budget_trial_limit_micros": 100000,
            "budget_task_limit_micros": 20000,
            "budget_input_price_micros_per_million": 1000000,
            "budget_output_price_micros_per_million": 1000000,
        }
    )
    yield client, db, request_id, guard, LearningBudgetService(db.session_factory, settings)


async def test_reservation_replay_and_usage_are_idempotent(reserved_context):
    _, db, request_id, guard, budget = reserved_context
    identity = await budget.reserve(request_id, "stable-call", 100, guard=guard)
    assert identity == await budget.reserve(request_id, "stable-call", 100, guard=guard)
    with pytest.raises(LearningError, match="identity_conflict"):
        await budget.reserve(request_id, "stable-call", 101, guard=guard)
    await budget.mark_dispatched(identity, guard=guard)
    with pytest.raises(LearningError, match="already_sent"):
        await budget.reserve(request_id, "stable-call", 100, guard=guard)
    usage = TokenUsage(input_tokens=10, output_tokens=20, total_tokens=30)
    assert await budget.settle(identity, usage, provider="test", model="test") == 30
    assert await budget.settle(identity, usage, provider="test", model="test") == 30
    async with db.session_factory() as session:
        assert len(list(await session.scalars(select(SpendRecord)))) == 1
        row = await session.get(LearningSpendReservationRecord, identity)
        assert row.status == "settled" and row.actual_micros == 30


async def test_unknown_usage_keeps_upper_bound_and_cannot_release(reserved_context):
    _, db, request_id, guard, budget = reserved_context
    identity = await budget.reserve(request_id, "unknown", 10000, guard=guard)
    await budget.mark_dispatched(identity, guard=guard)
    await budget.settle(identity, None, provider="test", model="test")
    with pytest.raises(LearningError, match="may_have_been_sent"):
        await budget.release_before_dispatch(identity)
    with pytest.raises(LearningError, match="waiting_budget"):
        await budget.reserve(request_id, "another", 1, guard=guard)
    async with db.session_factory() as session:
        assert (await session.get(LearningSpendReservationRecord, identity)).status == "unknown"
        assert not list(await session.scalars(select(SpendRecord)))


@pytest.mark.parametrize("sent,expected", [(False, "released"), (True, "unknown")])
async def test_stale_reconciliation_distinguishes_sent_identity(reserved_context, sent, expected):
    _, db, request_id, guard, budget = reserved_context
    identity = await budget.reserve(request_id, "stale", 100, guard=guard)
    if sent:
        await budget.mark_dispatched(identity, guard=guard)
    assert await budget.reconcile_stale() == 0
    async with db.session_factory() as session:
        job = await session.get(MaintenanceJobRecord, guard.job_id)
        job.lease_expires_at = datetime.now(UTC) - timedelta(seconds=60)
        await session.commit()
    assert await budget.reconcile_stale() == 1
    assert await budget.reconcile_stale() == 0
    async with db.session_factory() as session:
        row = await session.get(LearningSpendReservationRecord, identity)
        assert row.status == expected and row.reserved_micros == 100


async def test_source_revocation_blocks_prepared_dispatch(reserved_context):
    client, db, request_id, guard, budget = reserved_context
    identity = await budget.reserve(request_id, "withdrawn", 100, guard=guard)
    state = (await client.get(f"/api/v1/learning-requests/{request_id}")).json()
    response = await client.post(
        f"/api/v1/learning-sources/{state['source']['id']}/revoke",
        json={"reason": "withdraw", "expected_status": "valid"},
    )
    assert response.status_code == 200
    with pytest.raises(LearningError):
        await budget.mark_dispatched(identity, guard=guard)
    async with db.session_factory() as session:
        assert (await session.get(LearningSpendReservationRecord, identity)).dispatched_at is None


async def test_policy_reduction_after_reserve_blocks_dispatch(reserved_context):
    client, db, request_id, guard, budget = reserved_context
    identity = await budget.reserve(request_id, "reduced", 100, guard=guard)
    from evoagent.db.models import DEFAULT_WORKSPACE_ID

    changed = await client.put(
        f"/api/v1/workspaces/{DEFAULT_WORKSPACE_ID}/learning-policy",
        json={
            "mode": "manual",
            "expected_lock_version": 1,
            "daily_limit_micros": 50,
            "request_limit_micros": 50,
        },
    )
    assert changed.status_code == 200
    with pytest.raises(LearningError, match="waiting_budget"):
        await budget.mark_dispatched(identity, guard=guard)
    async with db.session_factory() as session:
        assert (await session.get(LearningSpendReservationRecord, identity)).dispatched_at is None


async def test_provider_wrapper_settles_reported_usage_once(reserved_context):
    _, db, request_id, guard, budget = reserved_context
    from evoagent.core.models import FinishReason, Message, MessageRole, ModelRequest, ModelResponse
    from evoagent.learning.provider import LearningBudgetedProvider
    from evoagent.providers.mock import MockProvider

    async def check():
        async with db.session_factory() as session:
            await guard.check(session)

    raw = MockProvider(
        [
            ModelResponse(
                message=Message(role=MessageRole.ASSISTANT, content="safe"),
                finish_reason=FinishReason.STOP,
                usage=TokenUsage(input_tokens=10, output_tokens=20, total_tokens=30),
            )
        ]
    )
    provider = LearningBudgetedProvider(
        raw, budget, request_id, guard, check=check, provider_name="offline-test"
    )
    request = ModelRequest(
        model="test",
        max_output_tokens=100,
        messages=(Message(role=MessageRole.USER, content="input"),),
    )
    events = [event async for event in provider.stream(request)]
    assert events and len(raw.requests) == 1
    with pytest.raises(LearningError, match="already_sent"):
        _ = [event async for event in provider.stream(request)]
    async with db.session_factory() as session:
        rows = list(await session.scalars(select(SpendRecord)))
        assert len(rows) == 1 and rows[0].cost_micros == 30
        assert rows[0].purpose == "skill_learning"


async def test_unsent_reservation_can_transfer_after_stale_lease_reconciliation(reserved_context):
    _, db, request_id, guard, budget = reserved_context
    identity = await budget.reserve(request_id, "takeover", 100, guard=guard)
    async with db.session_factory() as session:
        job = await session.get(MaintenanceJobRecord, guard.job_id)
        job.lease_expires_at = datetime.now(UTC) - timedelta(seconds=60)
        await session.commit()
    assert await budget.reconcile_stale() == 1
    async with db.session_factory() as session:
        job = await session.get(MaintenanceJobRecord, guard.job_id)
        job.lease_owner, job.lease_epoch = "replacement", guard.epoch + 1
        job.lease_expires_at = datetime.now(UTC) + timedelta(seconds=120)
        await session.commit()
    replacement = LearningJobGuard(guard.job_id, "replacement", guard.epoch + 1)
    assert await budget.reserve(request_id, "takeover", 100, guard=replacement) == identity
    with pytest.raises(LearningError, match="fenced"):
        await budget.mark_dispatched(identity, guard=guard)
    await budget.mark_dispatched(identity, guard=replacement)
    async with db.session_factory() as session:
        row = await session.get(LearningSpendReservationRecord, identity)
        assert row.job_epoch == replacement.epoch and row.dispatched_at is not None


async def test_malformed_settlement_remains_a_budget_upper_bound(reserved_context):
    _, db, request_id, guard, budget = reserved_context
    identity = await budget.reserve(request_id, "malformed", 10000, guard=guard)
    async with db.session_factory() as session:
        row = await session.get(LearningSpendReservationRecord, identity)
        row.status, row.actual_micros = "settled", None
        await session.commit()
    with pytest.raises(LearningError, match="waiting_budget"):
        await budget.reserve(request_id, "more", 1, guard=guard)


async def test_concurrent_calls_in_one_workspace_have_one_reservation(reserved_context):
    import asyncio

    _, db, request_id, guard, budget = reserved_context
    results = await asyncio.gather(
        budget.reserve(request_id, "parallel-a", 100, guard=guard),
        budget.reserve(request_id, "parallel-b", 100, guard=guard),
        return_exceptions=True,
    )
    assert sum(isinstance(result, UUID) for result in results) == 1
    rejected = next(result for result in results if isinstance(result, LearningError))
    assert rejected.code == "learning_workspace_call_busy"
    async with db.session_factory() as session:
        assert len(list(await session.scalars(select(LearningSpendReservationRecord)))) == 1


async def test_dispatch_uses_current_utc_day_not_reservation_day(reserved_context, monkeypatch):
    from evoagent.learning import budget as module
    from evoagent.tasks.lease_guard import database_now

    _, db, request_id, guard, budget = reserved_context

    async def previous_day(session):
        return await database_now(session) - timedelta(days=1)

    monkeypatch.setattr(module, "database_now", previous_day)
    identity = await budget.reserve(request_id, "midnight", 100, guard=guard)
    async with db.session_factory() as session:
        prior = (await session.get(LearningSpendReservationRecord, identity)).budget_day
    monkeypatch.setattr(module, "database_now", database_now)
    await budget.mark_dispatched(identity, guard=guard)
    async with db.session_factory() as session:
        row = await session.get(LearningSpendReservationRecord, identity)
        assert row.budget_day != prior
        assert row.budget_day == (await database_now(session)).astimezone(UTC).date().isoformat()
