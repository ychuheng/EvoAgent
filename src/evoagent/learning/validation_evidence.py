"""Bounded persisted payment evidence; report declarations are not authority."""

from sqlalchemy import select

from evoagent.db.models import EvalRunRecord, LearningSpendReservationRecord, RunRecord
from evoagent.learning.schema import LearningError


async def validation_cost(session, request):
    cap = request.policy_snapshot.get("maximum_model_calls")
    if type(cap) is not int or not 1 <= cap <= 6000:
        raise LearningError("validation_call_bound_invalid")
    evaluations = list(
        await session.scalars(
            select(EvalRunRecord)
            .where(EvalRunRecord.experiment_id == request.validation_experiment_id)
            .limit(101)
        )
    )
    if not 2 <= len(evaluations) <= 100:
        raise LearningError("validation_execution_binding_invalid")
    bindings = {row.run_id: row.task_id for row in evaluations}
    rows = list(
        await session.scalars(
            select(LearningSpendReservationRecord)
            .where(
                LearningSpendReservationRecord.request_id == request.id,
                LearningSpendReservationRecord.dispatcher_kind == "task",
            )
            .limit(6001)
        )
    )
    if len(rows) > 6000 or sum(row.status != "released" for row in rows) > cap:
        raise LearningError("validation_call_bound_exceeded")
    paid_runs = set()
    complete = True
    for row in rows:
        run = await session.get(RunRecord, row.run_id)
        if (
            row.workspace_id != request.workspace_id
            or bindings.get(row.run_id) != row.task_id
            or run is None
            or run.task_id != row.task_id
            or run.provider != request.policy_snapshot["provider"]
            or run.model != request.policy_snapshot["model"]
            or row.request_body_hash is None
        ):
            raise LearningError("validation_payment_binding_invalid")
        if row.dispatched_at is not None:
            paid_runs.add(row.run_id)
            complete = complete and row.status == "settled" and row.actual_micros is not None
        else:
            complete = complete and row.status == "released"
    return {
        "provider": request.policy_snapshot["provider"],
        "paid_calls": sum(row.dispatched_at is not None for row in rows),
        "usage_complete": complete and paid_runs == set(bindings),
        "known_spent_micros": sum(row.actual_micros or 0 for row in rows),
    }


async def verify_report_cost(session, request, report):
    if report.get("cost") != await validation_cost(session, request):
        raise LearningError("validation_payment_report_invalid")
