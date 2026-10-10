"""Synthetic accounting only: no network model calls or approved real spending."""

import asyncio
from dataclasses import replace

import pytest
from sqlalchemy import select

from evoagent.core.models import (
    FinishReason,
    Message,
    MessageRole,
    ModelRequest,
    ModelResponse,
    TokenUsage,
)
from evoagent.db.models import (
    DEFAULT_WORKSPACE_ID,
    LearningPolicyRecord,
    LearningSpendReservationRecord,
    SpendRecord,
    TaskRecord,
)
from evoagent.learning.budget import LearningBudgetService
from evoagent.learning.provider import LearningBudgetedProvider
from evoagent.learning.schema import LearningError
from evoagent.learning.task_budget_guard import ValidationTaskBudgetGuard
from evoagent.learning.validation_guard import PersonalValidationRunGuard
from evoagent.providers.mock import MockProvider
from evoagent.runtime.budget import BudgetScope, evaluate_budget
from evoagent.skills.canonical import content_hash
from evoagent.tasks.lease import JobLeaseManager
from evoagent.tasks.lease_guard import LeaseLostError
from evoagent.trace.artifacts import LocalArtifactStore
from tests.integration.test_personal_validation_replicas import prepare

pytest_plugins = ("tests.integration.test_personal_trials",)


async def apply_ledger_migration(db, direction):
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from alembic.script import ScriptDirectory

    from evoagent.db.base import Base
    from tests.integration.test_migrations import alembic_config

    revision = (
        ScriptDirectory.from_config(
            alembic_config(db.engine.url.render_as_string(hide_password=False))
        )
        .get_revision("20261010_0030")
        .module
    )

    def apply(connection):
        context = MigrationContext.configure(connection, opts={"target_metadata": Base.metadata})
        with Operations.context(context):
            getattr(revision, direction)()

    # Use the fixture's actual schema-bound engine; a URL alone loses its
    # search_path and must never stamp or migrate the shared public schema.
    async with db.engine.begin() as connection:
        await connection.run_sync(apply)


@pytest.fixture
async def task_budget_context(trial_candidate):
    db = trial_candidate[1]
    async with db.session_factory() as session:
        policy = await session.get(LearningPolicyRecord, DEFAULT_WORKSPACE_ID)
        policy.daily_limit_micros = 50000
        policy.request_limit_micros = 20000
        await session.commit()
    db, settings, request, _ = await prepare(trial_candidate)
    settings = settings.model_copy(
        update={
            "budget_scope": "trial",
            "budget_trial_limit_micros": 100000,
            "budget_task_limit_micros": 20000,
            "budget_input_price_micros_per_million": 1000000,
            "budget_output_price_micros_per_million": 1000000,
        }
    )
    lease = await JobLeaseManager(db.session_factory, lease_seconds=300).claim_next("budget-task")
    validation = await PersonalValidationRunGuard.for_run(
        db.session_factory,
        LocalArtifactStore(settings.artifact_root),
        lease.run_id,
        learning_enabled=True,
    )
    return (
        db,
        request,
        ValidationTaskBudgetGuard(lease, validation),
        LearningBudgetService(db.session_factory, settings),
    )


async def test_task_identity_and_usage_belong_to_execution_not_source(task_budget_context):
    db, request, guard, budget = task_budget_context
    body_hash = content_hash({"step": 1})
    identity = await budget.reserve(
        request.id, "task-first", 100, guard=guard, request_body_hash=body_hash
    )
    assert (
        await budget.reserve(
            request.id, "task-first", 100, guard=guard, request_body_hash=body_hash
        )
        == identity
    )
    with pytest.raises(LearningError, match="identity_conflict"):
        await budget.reserve(
            request.id,
            "task-first",
            100,
            guard=guard,
            request_body_hash=content_hash({"changed": True}),
        )
    await budget.mark_dispatched(identity, guard=guard)
    usage = TokenUsage(input_tokens=10, output_tokens=20, total_tokens=30)
    await budget.settle(identity, usage, provider="synthetic", model="synthetic")
    await budget.settle(identity, usage, provider="synthetic", model="synthetic")
    async with db.session_factory() as session:
        reservation = await session.get(LearningSpendReservationRecord, identity)
        receipts = list(
            await session.scalars(select(SpendRecord).where(SpendRecord.reservation_id == identity))
        )
    assert reservation.dispatcher_kind == "task" and reservation.job_id is None
    assert reservation.task_id == guard.lease.task_id and reservation.run_id == guard.lease.run_id
    assert len(receipts) == 1 and receipts[0].run_id == guard.lease.run_id
    assert receipts[0].run_id != request.origin_run_id


@pytest.mark.parametrize("sent", [False, True])
async def test_task_fencing_reconciliation_and_unsent_transfer(task_budget_context, sent):
    db, request, guard, budget = task_budget_context
    body_hash = content_hash("initial")
    identity = await budget.reserve(
        request.id, "task-transfer", 100, guard=guard, request_body_hash=body_hash
    )
    assert await budget.reconcile_stale() == 0
    if sent:
        await budget.mark_dispatched(identity, guard=guard)
    async with db.session_factory() as session:
        task = await session.get(TaskRecord, guard.lease.task_id)
        task.lease_epoch += 1
        await session.commit()
    assert await budget.reconcile_stale() == 1
    assert await budget.reconcile_stale() == 0
    with pytest.raises(LeaseLostError):
        await budget.mark_dispatched(identity, guard=guard)
    successor = ValidationTaskBudgetGuard(
        replace(guard.lease, epoch=guard.lease.epoch + 1), guard.validation_guard
    )
    if sent:
        with pytest.raises(LearningError, match="already_sent"):
            await budget.reserve(
                request.id, "task-transfer", 100, guard=successor, request_body_hash=body_hash
            )
    else:
        assert (
            await budget.reserve(
                request.id, "task-transfer", 100, guard=successor, request_body_hash=body_hash
            )
            == identity
        )
        await budget.mark_dispatched(identity, guard=successor)


async def test_unknown_task_usage_counts_only_for_bound_task(task_budget_context):
    db, request, guard, budget = task_budget_context
    identity = await budget.reserve(
        request.id, "unknown-task", 100, guard=guard, request_body_hash=content_hash("step")
    )
    await budget.mark_dispatched(identity, guard=guard)
    await budget.settle(identity, None, provider="synthetic", model="synthetic")
    async with db.session_factory() as session:
        status = await evaluate_budget(
            session, budget.settings, scope=BudgetScope.TRIAL, task_id=guard.lease.task_id
        )
    assert status.task_spent_micros == 100 and status.spent_micros == 100


async def test_provider_step_identity_prevents_resend_and_distinguishes_same_body(
    task_budget_context,
):
    db, request, guard, budget = task_budget_context
    usage = TokenUsage(input_tokens=10, output_tokens=20, total_tokens=30)
    response = ModelResponse(
        message=Message(role=MessageRole.ASSISTANT, content="done"),
        finish_reason=FinishReason.STOP,
        usage=usage,
    )
    raw = MockProvider([response, response])
    provider = LearningBudgetedProvider(
        raw,
        budget,
        request.id,
        guard,
        check=guard.validation_guard.check,
        provider_name="synthetic",
    )
    call = ModelRequest(
        messages=(Message(role=MessageRole.USER, content="same input"),),
        model="mock",
        max_output_tokens=100,
        runtime_iteration=1,
    )
    assert "runtime_iteration" not in call.model_dump(mode="json")
    assert [event async for event in provider.stream(call)]
    with pytest.raises(LearningError, match="already_sent"):
        [event async for event in provider.stream(call)]
    assert [
        event async for event in provider.stream(call.model_copy(update={"runtime_iteration": 2}))
    ]
    assert len(raw.requests) == 2
    async with db.session_factory() as session:
        rows = list(
            await session.scalars(
                select(LearningSpendReservationRecord).where(
                    LearningSpendReservationRecord.request_id == request.id
                )
            )
        )
    assert len(rows) == 2 and rows[0].request_body_hash == rows[1].request_body_hash


async def test_two_task_workers_share_workspace_reservation_gate(task_budget_context):
    db, request, first, budget = task_budget_context
    if db.engine.dialect.name != "postgresql":
        pytest.skip("row lock concurrency requires isolated PostgreSQL")
    lease = await JobLeaseManager(db.session_factory, lease_seconds=300).claim_next("budget-other")
    validation = await PersonalValidationRunGuard.for_run(
        db.session_factory, first.validation_guard.store, lease.run_id, learning_enabled=True
    )
    second = ValidationTaskBudgetGuard(lease, validation)
    results = await asyncio.gather(
        *[
            budget.reserve(
                request.id,
                f"parallel-{index}",
                100,
                guard=guard,
                request_body_hash=content_hash("same"),
            )
            for index, guard in enumerate((first, second))
        ],
        return_exceptions=True,
    )
    assert sum(not isinstance(item, Exception) for item in results) == 1
    failure = next(item for item in results if isinstance(item, Exception))
    assert isinstance(failure, LearningError) and "workspace_call_busy" in str(failure)


async def test_task_cancel_flag_prevents_reserve_even_with_valid_lease(task_budget_context):
    db, request, guard, budget = task_budget_context
    async with db.session_factory() as session:
        task = await session.get(TaskRecord, guard.lease.task_id)
        task.cancel_requested = True
        await session.commit()
    with pytest.raises(LearningError, match="task_cancelled"):
        await budget.reserve(
            request.id, "cancelled-task", 100, guard=guard, request_body_hash=content_hash("body")
        )
    async with db.session_factory() as session:
        assert not list(await session.scalars(select(LearningSpendReservationRecord)))


async def test_task_accounting_evidence_blocks_destructive_downgrade(task_budget_context):
    db, request, guard, budget = task_budget_context
    await budget.reserve(
        request.id, "migration-task", 100, guard=guard, request_body_hash=content_hash("body")
    )
    with pytest.raises(RuntimeError, match="prevents destructive downgrade"):
        await apply_ledger_migration(db, "downgrade")
    async with db.session_factory() as session:
        rows = list(await session.scalars(select(LearningSpendReservationRecord)))
    assert len(rows) == 1 and rows[0].dispatcher_kind == "task"


async def test_legacy_reservation_survives_roundtrip_without_task_reclassification(
    task_budget_context,
):
    db, request, _, _ = task_budget_context
    legacy = LearningSpendReservationRecord(
        workspace_id=request.workspace_id,
        request_id=request.id,
        call_key="legacy-before-task-ledger",
        budget_day="2026-10-01",
        reserved_micros=100,
        status="unknown",
        scope="trial",
    )
    async with db.session_factory() as session:
        session.add(legacy)
        await session.commit()
    await apply_ledger_migration(db, "downgrade")
    await apply_ledger_migration(db, "upgrade")
    async with db.session_factory() as session:
        restored = await session.get(LearningSpendReservationRecord, legacy.id)
    assert restored.dispatcher_kind == "maintenance" and restored.task_id is None
    assert restored.run_id is None and restored.request_body_hash is None
    assert restored.status == "unknown" and restored.reserved_micros == 100
    assert restored.budget_day == "2026-10-01" and restored.request_id == request.id
