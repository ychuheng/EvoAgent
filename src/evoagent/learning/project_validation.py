"""Project scope may authorize a Skill, never mount the user's project in validation."""

from evoagent.learning.schema import LearningError


def require_project_replica_contract(request):
    if request.project_id is None:
        return
    if (
        request.policy_snapshot.get("project_replica_policy") != "registered-fixtures:v1"
        or request.policy_snapshot.get("source_project_id") != str(request.project_id)
        or not request.frozen_inputs.get("validation_cases")
        or any(not case.get("fixture_id") for case in request.frozen_inputs["validation_cases"])
    ):
        raise LearningError("validation_project_replica_required")


async def selection_project_matches(session, run, task, scope, *, factory):
    if task.project_id == scope.project_id:
        return True
    if task.project_id is not None or scope.project_id is None or run.data_role != "dev":
        return False
    from sqlalchemy import select

    from evoagent.db.models import EvalExperimentRecord, EvalRunRecord, LearningRequestRecord
    from evoagent.learning.sources import PersonalSourceService
    from evoagent.skills.canonical import content_hash

    requests = list(
        await session.scalars(
            select(LearningRequestRecord)
            .join(
                EvalExperimentRecord,
                EvalExperimentRecord.learning_request_id == LearningRequestRecord.id,
            )
            .join(EvalRunRecord, EvalRunRecord.experiment_id == EvalExperimentRecord.id)
            .where(
                EvalRunRecord.run_id == run.id,
                EvalRunRecord.task_id == task.id,
                EvalExperimentRecord.purpose == "personal_validation",
                EvalExperimentRecord.id == LearningRequestRecord.validation_experiment_id,
                LearningRequestRecord.project_id == scope.project_id,
                LearningRequestRecord.workspace_id == scope.workspace_id,
                LearningRequestRecord.request_kind == "validate",
                LearningRequestRecord.status == "running",
                LearningRequestRecord.stage == "waiting_validation",
            )
            .limit(2)
        )
    )
    if len(requests) != 1:
        return False
    request = requests[0]
    try:
        require_project_replica_contract(request)
        if content_hash(
            request.policy_snapshot
        ) != request.policy_hash or request.policy_hash != request.frozen_inputs.get(
            "validation_policy_hash"
        ):
            return False
        from uuid import UUID

        await PersonalSourceService(
            factory, max_source_risk=request.policy_snapshot["max_source_risk"]
        ).check_in_session(
            session,
            request.origin_run_id,
            UUID(request.frozen_inputs["feedback_id"])
            if request.frozen_inputs.get("feedback_id")
            else None,
            "feedback" if request.frozen_inputs.get("feedback_id") else "manual",
        )
    except LearningError:
        return False
    return True
