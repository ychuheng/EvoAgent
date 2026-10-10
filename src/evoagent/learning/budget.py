"""Durable per-workspace learning reservations; unknown usage is never free."""

import re
from datetime import UTC
from uuid import uuid4

from sqlalchemy import func, or_, select

from evoagent.db.learning_accounting import unresolved_usage, usage_upper_bound
from evoagent.db.models import (
    LearningPolicyRecord,
    LearningRequestRecord,
    LearningSpendReservationRecord,
    MaintenanceJobRecord,
    RunRecord,
    SpendRecord,
    TaskRecord,
)
from evoagent.learning.schema import LearningError
from evoagent.learning.sources import PersonalSourceService
from evoagent.runtime.budget import (
    BudgetScope,
    cost_micros,
    limits_from_settings,
    spend_totals,
    threshold_micros,
)
from evoagent.tasks.lease_guard import database_now


class LearningBudgetService:
    def __init__(self, factory, settings):
        self.factory, self.settings = factory, settings

    async def _lock(self, session, request_id, guard=None):
        if guard is not None and hasattr(guard, "lock_lease"):
            await guard.lock_lease(session)
        row = await session.get(LearningRequestRecord, request_id)
        if row is None:
            raise LearningError("learning_request_not_found")
        await PersonalSourceService(self.factory)._lock_run_scope(session, row.origin_run_id)
        # Different workspaces share the global budget. Their independent row
        # locks alone cannot serialize a global check-and-reserve operation.
        if session.get_bind().dialect.name == "postgresql":
            await session.execute(select(func.pg_advisory_xact_lock(1062026, 1)))
        row = await session.scalar(
            select(LearningRequestRecord)
            .where(LearningRequestRecord.id == request_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return row

    _upper_bound = staticmethod(usage_upper_bound)

    @staticmethod
    def _binding(guard):
        if hasattr(guard, "reservation_binding"):
            return guard.reservation_binding()
        return dict(
            dispatcher_kind="maintenance",
            job_id=guard.job_id,
            job_epoch=guard.epoch,
            task_id=None,
            run_id=None,
            task_epoch=None,
        )

    async def _accounting_task(self, session, row, request):
        return (
            row.task_id
            if row.dispatcher_kind == "task"
            else (await session.get(RunRecord, request.origin_run_id)).task_id
        )

    async def reserve(
        self, request_id, call_key, max_cost_micros, *, guard, request_body_hash=None
    ):
        binding = self._binding(guard)
        if binding["dispatcher_kind"] == "task" and (
            request_body_hash is None or not re.fullmatch(r"sha256:[a-f0-9]{64}", request_body_hash)
        ):
            raise LearningError("validation_call_body_hash_required")
        if max_cost_micros < 0 or max_cost_micros > 2147483647:
            raise LearningError("invalid_learning_cost_bound")
        async with self.factory() as session:
            request = await self._lock(session, request_id, guard)
            job, checked = await guard.check(session)
            if checked.id != request_id:
                raise LearningError("learning_job_fenced")
            now = await database_now(session)
            day = now.astimezone(UTC).date().isoformat()
            policy = await session.get(LearningPolicyRecord, request.workspace_id)
            if not self.settings.learning_enabled or policy is None or policy.mode == "off":
                raise LearningError("learning_policy_off")
            old = await session.scalar(
                select(LearningSpendReservationRecord)
                .where(LearningSpendReservationRecord.call_key == call_key)
                .with_for_update()
            )
            if old is not None:
                if (
                    old.request_id != request_id
                    or old.reserved_micros != max_cost_micros
                    or old.request_body_hash != request_body_hash
                    or any(
                        getattr(old, key) != binding[key]
                        for key in ("dispatcher_kind", "task_id", "run_id")
                    )
                ):
                    raise LearningError("learning_call_identity_conflict")
                if old.dispatched_at is not None or old.status not in {"reserved", "released"}:
                    raise LearningError("learning_call_already_sent")
                # An undispatched reservation can transfer to a new job lease.
                if old.status == "reserved":
                    for key, value in binding.items():
                        setattr(old, key, value)
                    await session.commit()
                    return old.id
            limits = limits_from_settings(self.settings, BudgetScope(self.settings.budget_scope))
            if not limits.priced or limits.limit_micros is None:
                raise LearningError("learning_budget_unapproved")
            amount = self._upper_bound()
            daily = await session.scalar(
                select(func.coalesce(func.sum(amount), 0)).where(
                    LearningSpendReservationRecord.workspace_id == request.workspace_id,
                    LearningSpendReservationRecord.budget_day == day,
                )
            )
            total = await session.scalar(
                select(func.coalesce(func.sum(amount), 0)).where(
                    LearningSpendReservationRecord.request_id == request_id
                )
            )
            for key, spent in (("daily_limit_micros", daily), ("request_limit_micros", total)):
                values = (getattr(policy, key), request.policy_snapshot.get(key))
                if any(value is None for value in values) or spent + max_cost_micros > min(values):
                    raise LearningError("learning_waiting_budget")
            if await session.scalar(
                select(LearningSpendReservationRecord.id)
                .where(
                    LearningSpendReservationRecord.workspace_id == request.workspace_id,
                    LearningSpendReservationRecord.status == "reserved",
                )
                .limit(1)
            ):
                raise LearningError("learning_workspace_call_busy")
            global_known, task_known = await spend_totals(
                session,
                scope=limits.scope,
                task_id=binding["task_id"]
                or (await session.get(RunRecord, request.origin_run_id)).task_id,
            )
            unresolved = await session.scalar(
                select(
                    func.coalesce(func.sum(LearningSpendReservationRecord.reserved_micros), 0)
                ).where(
                    LearningSpendReservationRecord.scope.in_((limits.scope.value, "legacy")),
                    unresolved_usage(),
                )
            )
            if global_known + unresolved + max_cost_micros > threshold_micros(limits):
                raise LearningError("learning_waiting_budget")
            request_unresolved = await session.scalar(
                select(
                    func.coalesce(func.sum(LearningSpendReservationRecord.reserved_micros), 0)
                ).where(
                    (LearningSpendReservationRecord.task_id == binding["task_id"])
                    if binding["dispatcher_kind"] == "task"
                    else (LearningSpendReservationRecord.request_id == request_id),
                    unresolved_usage(),
                )
            )
            if (
                limits.task_limit_micros is not None
                and task_known + request_unresolved + max_cost_micros > limits.task_limit_micros
            ):
                raise LearningError("learning_waiting_budget")
            values = dict(
                workspace_id=request.workspace_id,
                request_id=request.id,
                call_key=call_key,
                budget_day=day,
                reserved_micros=max_cost_micros,
                status="reserved",
                **binding,
                request_body_hash=request_body_hash,
                scope=limits.scope.value,
                input_price_micros_per_million=limits.input_price_micros_per_million,
                output_price_micros_per_million=limits.output_price_micros_per_million,
            )
            row = old or LearningSpendReservationRecord(id=uuid4(), **values)
            if old is not None:
                for field, value in values.items():
                    setattr(row, field, value)
                row.actual_micros = None
            session.add(row)
            await session.commit()
            return row.id

    async def mark_dispatched(self, reservation_id, *, guard):
        async with self.factory() as session:
            initial = await session.get(LearningSpendReservationRecord, reservation_id)
            if initial is None:
                raise LearningError("learning_reservation_not_found")
            request = await self._lock(session, initial.request_id, guard)
            _, checked = await guard.check(session)
            row = await session.scalar(
                select(LearningSpendReservationRecord)
                .where(LearningSpendReservationRecord.id == reservation_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            policy = await session.get(LearningPolicyRecord, request.workspace_id)
            if policy is None or policy.mode == "off" or not self.settings.learning_enabled:
                raise LearningError("learning_policy_off")
            from evoagent.db.models import ArtifactRecord, LearningSourceRecord

            source = await session.scalar(
                select(LearningSourceRecord)
                .where(
                    LearningSourceRecord.run_id == request.origin_run_id,
                    LearningSourceRecord.source_revision
                    == request.frozen_inputs["source_revision"],
                )
                .with_for_update()
            )
            artifact = await session.get(ArtifactRecord, source.artifact_id) if source else None
            if (
                source is None
                or source.status != "valid"
                or artifact is None
                or artifact.attributes.get("erased")
                or artifact.redaction_status == "quarantined"
            ):
                raise LearningError("learning_source_revoked")
            await PersonalSourceService(
                self.factory,
                max_source_risk=min(
                    policy.max_source_risk, request.policy_snapshot["max_source_risk"]
                ),
            ).check_in_session(
                session,
                request.origin_run_id,
                source.feedback_id,
                "feedback" if source.feedback_id else "manual",
            )
            if (
                row.status != "reserved"
                or row.dispatched_at is not None
                or any(getattr(row, key) != value for key, value in self._binding(guard).items())
                or checked.id != request.id
            ):
                raise LearningError("learning_call_already_sent")
            now = await database_now(session)
            row.budget_day = now.astimezone(UTC).date().isoformat()
            await session.flush()
            amount = self._upper_bound()
            for key, condition in (
                (
                    "daily_limit_micros",
                    (LearningSpendReservationRecord.workspace_id == request.workspace_id)
                    & (LearningSpendReservationRecord.budget_day == row.budget_day),
                ),
                ("request_limit_micros", LearningSpendReservationRecord.request_id == request.id),
            ):
                used = await session.scalar(
                    select(func.coalesce(func.sum(amount), 0)).where(condition)
                )
                limits = (getattr(policy, key), request.policy_snapshot.get(key))
                if any(value is None for value in limits) or used > min(limits):
                    raise LearningError("learning_waiting_budget")
            from evoagent.runtime.budget import evaluate_budget

            status = await evaluate_budget(
                session,
                self.settings,
                scope=BudgetScope(row.scope),
                task_id=await self._accounting_task(session, row, request),
            )
            # This reservation is already included in status.spent. Reaching the
            # threshold is allowed only exactly at the bound, never beyond it.
            if (
                status.stop_threshold_micros is None
                or status.spent_micros > status.stop_threshold_micros
                or not limits_from_settings(self.settings, BudgetScope(row.scope)).priced
            ):
                raise LearningError("learning_waiting_budget")
            if (
                status.task_limit_micros is not None
                and status.task_spent_micros > status.task_limit_micros
            ):
                raise LearningError("learning_waiting_budget")
            # Commit the send intent BEFORE touching the remote provider. A crash
            # in the narrow following gap is deliberately treated as unknown.
            row.dispatched_at = now
            await session.commit()

    async def mark_unknown(self, reservation_id):
        async with self.factory() as session:
            row = await session.scalar(
                select(LearningSpendReservationRecord)
                .where(LearningSpendReservationRecord.id == reservation_id)
                .with_for_update()
            )
            if row and row.status == "reserved":
                row.status = "unknown"
            await session.commit()

    async def release_before_dispatch(self, reservation_id):
        async with self.factory() as session:
            row = await session.scalar(
                select(LearningSpendReservationRecord)
                .where(LearningSpendReservationRecord.id == reservation_id)
                .with_for_update()
            )
            if row is None or row.dispatched_at is not None:
                raise LearningError("learning_call_may_have_been_sent")
            if row.status == "reserved":
                row.status = "released"
            await session.commit()

    async def settle(self, reservation_id, usage, *, provider, model):
        if usage is None:
            return await self.mark_unknown(reservation_id)
        async with self.factory() as session:
            row = await session.scalar(
                select(LearningSpendReservationRecord)
                .where(LearningSpendReservationRecord.id == reservation_id)
                .with_for_update()
            )
            if row is None or row.dispatched_at is None or row.status == "released":
                raise LearningError("learning_settlement_without_dispatch")
            limits = limits_from_settings(self.settings, BudgetScope(row.scope))
            from dataclasses import replace

            limits = replace(
                limits,
                input_price_micros_per_million=row.input_price_micros_per_million,
                output_price_micros_per_million=row.output_price_micros_per_million,
            )
            actual = cost_micros(
                limits, input_tokens=usage.input_tokens, output_tokens=usage.output_tokens
            )
            old = await session.scalar(
                select(SpendRecord).where(SpendRecord.reservation_id == row.id)
            )
            if old is not None:
                if (
                    old.input_tokens,
                    old.output_tokens,
                    old.cost_micros,
                    old.model,
                    old.provider,
                ) != (usage.input_tokens, usage.output_tokens, actual, model, provider):
                    raise LearningError("learning_usage_conflict")
                return actual
            request = await session.get(LearningRequestRecord, row.request_id)
            run = await session.get(RunRecord, row.run_id or request.origin_run_id)
            session.add(
                SpendRecord(
                    purpose="skill_learning"
                    if request.request_kind == "propose"
                    else "skill_validation",
                    learning_request_id=request.id,
                    reservation_id=row.id,
                    scope=row.scope,
                    task_id=run.task_id,
                    run_id=run.id,
                    provider=provider,
                    model=model,
                    input_tokens=usage.input_tokens,
                    output_tokens=usage.output_tokens,
                    cost_micros=actual,
                    input_price_micros_per_million=row.input_price_micros_per_million,
                    output_price_micros_per_million=row.output_price_micros_per_million,
                )
            )
            row.status, row.actual_micros = "settled", actual
            await session.commit()
            return actual

    async def reconcile_stale(self, *, budget_day=None, limit=100):
        if not 1 <= limit <= 1000:
            raise LearningError("invalid_reconciliation_limit")
        changed = 0
        async with self.factory() as session:
            now = await database_now(session)
            statement = (
                select(LearningSpendReservationRecord)
                .where(LearningSpendReservationRecord.status == "reserved")
                .outerjoin(
                    MaintenanceJobRecord,
                    MaintenanceJobRecord.id == LearningSpendReservationRecord.job_id,
                )
                .outerjoin(TaskRecord, TaskRecord.id == LearningSpendReservationRecord.task_id)
                .outerjoin(RunRecord, RunRecord.id == LearningSpendReservationRecord.run_id)
                .where(
                    or_(
                        (LearningSpendReservationRecord.dispatcher_kind == "maintenance")
                        & or_(
                            MaintenanceJobRecord.id.is_(None),
                            MaintenanceJobRecord.status != "running",
                            MaintenanceJobRecord.lease_epoch
                            != LearningSpendReservationRecord.job_epoch,
                            MaintenanceJobRecord.lease_expires_at <= now,
                            MaintenanceJobRecord.cancel_requested.is_(True),
                        ),
                        (LearningSpendReservationRecord.dispatcher_kind == "task")
                        & or_(
                            TaskRecord.id.is_(None),
                            TaskRecord.status.not_in(("running", "waiting_tool")),
                            TaskRecord.lease_epoch != LearningSpendReservationRecord.task_epoch,
                            TaskRecord.lease_expires_at <= now,
                            TaskRecord.lease_expires_at.is_(None),
                            TaskRecord.lease_owner.is_(None),
                            TaskRecord.cancel_requested.is_(True),
                            RunRecord.status != "running",
                        ),
                    )
                )
                .order_by(LearningSpendReservationRecord.created_at)
                .limit(limit)
                .with_for_update(skip_locked=True, of=LearningSpendReservationRecord)
            )
            if budget_day is not None:
                statement = statement.where(LearningSpendReservationRecord.budget_day == budget_day)
            for row in await session.scalars(statement):
                if row.dispatcher_kind == "task":
                    task = await session.get(TaskRecord, row.task_id, populate_existing=True)
                    run = await session.get(RunRecord, row.run_id, populate_existing=True)
                    expiry = task.lease_expires_at if task else None
                    if expiry and expiry.tzinfo is None:
                        expiry = expiry.replace(tzinfo=UTC)
                    if (
                        task
                        and run
                        and task.status in {"running", "waiting_tool"}
                        and run.status == "running"
                        and task.lease_owner
                        and task.lease_epoch == row.task_epoch
                        and expiry
                        and expiry > now
                        and not task.cancel_requested
                    ):
                        continue
                    row.status = "unknown" if row.dispatched_at else "released"
                    changed += 1
                    continue
                job = await session.get(MaintenanceJobRecord, row.job_id) if row.job_id else None
                expiry = job.lease_expires_at if job else None
                if expiry and expiry.tzinfo is None:
                    expiry = expiry.replace(tzinfo=UTC)
                if (
                    job
                    and job.status == "running"
                    and job.lease_epoch == row.job_epoch
                    and expiry
                    and expiry > now
                    and not job.cancel_requested
                ):
                    continue
                row.status = "unknown" if row.dispatched_at or row.job_id is None else "released"
                changed += 1
            await session.commit()
        return changed
