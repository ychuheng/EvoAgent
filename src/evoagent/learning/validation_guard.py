"""Authorization for internal personal-validation runs; never grant user scopes."""

import json
from uuid import UUID

from sqlalchemy import select

from evoagent.db.models import (
    EvalCaseRecord,
    EvalExperimentRecord,
    EvalRunRecord,
    LearningPolicyRecord,
    LearningRequestRecord,
    LearningSourceRecord,
    RunRecord,
    SessionRecord,
    TaskRecord,
)
from evoagent.learning.sources import PersonalSourceService
from evoagent.learning.validation_schema import PersonalValidationCase
from evoagent.memory.schema import MemoryError
from evoagent.skills.access import SkillAccessError, SkillAccessPolicy
from evoagent.skills.canonical import content_hash
from evoagent.skills.lifecycle import SkillVersionStatus


class PersonalValidationRunGuard:
    def __init__(self, factory, store, run_id, evaluation_id, request_id, *, learning_enabled):
        self.factory, self.store = factory, store
        self.run_id, self.evaluation_id, self.request_id = run_id, evaluation_id, request_id
        self.enabled = learning_enabled

    @classmethod
    async def for_run(cls, factory, store, run_id, *, learning_enabled):
        async with factory() as session:
            rows = list(
                await session.execute(
                    select(EvalRunRecord.id, EvalExperimentRecord.learning_request_id)
                    .join(
                        EvalExperimentRecord, EvalExperimentRecord.id == EvalRunRecord.experiment_id
                    )
                    .where(
                        EvalRunRecord.run_id == run_id,
                        EvalExperimentRecord.purpose == "personal_validation",
                    )
                    .limit(2)
                )
            )
        if not rows:
            return None
        if len(rows) != 1 or rows[0][1] is None:
            raise MemoryError("personal_validation_identity_invalid")
        return cls(
            factory, store, run_id, rows[0][0], rows[0][1], learning_enabled=learning_enabled
        )

    async def _verify(self, session):
        if not self.enabled:
            raise MemoryError("personal_validation_disabled")
        evaluation = await session.get(EvalRunRecord, self.evaluation_id)
        request = await session.get(LearningRequestRecord, self.request_id)
        experiment = (
            await session.get(EvalExperimentRecord, evaluation.experiment_id)
            if evaluation
            else None
        )
        run = await session.get(RunRecord, self.run_id)
        task = await session.get(TaskRecord, run.task_id) if run else None
        chat = await session.get(SessionRecord, task.session_id) if task else None
        case = await session.get(EvalCaseRecord, evaluation.eval_case_id) if evaluation else None
        policy = await session.get(LearningPolicyRecord, request.workspace_id) if request else None
        if (
            request is None
            or request.request_kind != "validate"
            or request.status != "running"
            or request.stage != "waiting_validation"
            or request.project_id is not None
            or experiment is None
            or experiment.purpose != "personal_validation"
            or str(experiment.status) not in {"queued", "running"}
            or experiment.learning_request_id != request.id
            or request.validation_experiment_id != experiment.id
            or run is None
            or task is None
            or evaluation.run_id != self.run_id
            or evaluation.task_id != run.task_id
            or chat is None
            or chat.workspace_id != request.workspace_id
            or task.project_id is not None
            or task.cancel_requested
            or run.data_role != "dev"
            or policy is None
            or policy.mode == "off"
            or case is None
            or case.dataset_id != experiment.dataset_id
            or str(case.split) != "train"
            or task.goal != json.dumps(case.public_input, ensure_ascii=False, sort_keys=True)
            or run.provider != "mock"
            or run.model != "mock"
            or content_hash(request.policy_snapshot) != request.policy_hash
            or request.policy_hash != request.frozen_inputs.get("validation_policy_hash")
        ):
            raise MemoryError("personal_validation_authorization_revoked")
        try:
            frozen_case = next(
                PersonalValidationCase.model_validate(item)
                for item in request.frozen_inputs["validation_cases"]
                if item["case_key"] == case.case_key
            )
            source_id = UUID(request.frozen_inputs["source_id"])
        except (KeyError, ValueError, TypeError, StopIteration) as error:
            raise MemoryError("personal_validation_frozen_input_invalid") from error
        if (
            frozen_case.public_input != case.public_input
            or frozen_case.task_family != case.task_family
            or content_hash(case.public_input["inputs"])
            != request.frozen_inputs.get("input_fingerprints", {}).get(case.case_key)
        ):
            raise MemoryError("personal_validation_frozen_input_invalid")
        # The arm and the pinned body are server-created, not an API capability.
        version_id = (
            request.candidate_version_id
            if evaluation.arm == "treatment"
            else request.base_version_id
        )
        if (
            evaluation.skill_version_id != version_id
            or run.pinned_skill_version_id != version_id
            or run.run_mode != ("pinned_skill" if version_id is not None else "baseline")
            or experiment.skill_version_id != request.candidate_version_id
            or experiment.comparison_version_id != request.base_version_id
        ):
            raise MemoryError("personal_validation_identity_invalid")
        try:
            candidate = await SkillAccessPolicy().check(
                session,
                request.candidate_version_id,
                workspace_id=request.workspace_id,
                project_id=None,
            )
            if version_id is not None and version_id != request.candidate_version_id:
                comparison = await SkillAccessPolicy().check(
                    session, version_id, workspace_id=request.workspace_id, project_id=None
                )
                if (
                    comparison.skill_id != candidate.skill_id
                    or comparison.lifecycle_status is SkillVersionStatus.REJECTED
                ):
                    raise SkillAccessError("invalid comparison")
        except SkillAccessError as error:
            raise MemoryError("personal_validation_candidate_unavailable") from error
        if (
            candidate.lifecycle_status is not SkillVersionStatus.DRAFT
            or candidate.content_hash != request.frozen_inputs.get("candidate_content_hash")
        ):
            raise MemoryError("personal_validation_candidate_unavailable")
        source = await session.get(LearningSourceRecord, source_id)
        if (
            source is None
            or source.status != "valid"
            or source.run_id != request.origin_run_id
            or source.source_revision != request.frozen_inputs.get("source_revision")
            or source.content_hash != request.frozen_inputs.get("source_hash")
        ):
            raise MemoryError("personal_validation_source_revoked")
        return (
            source.id,
            source.revocation_epoch,
            min(policy.max_source_risk, request.policy_snapshot["max_source_risk"]),
        )

    async def check(self):
        async with self.factory() as session:
            source_id, epoch, max_risk = await self._verify(session)
        # A cached metadata hash cannot replace full current-policy inspection.
        try:
            await PersonalSourceService(
                self.factory, artifact_store=self.store, max_source_risk=max_risk
            ).read_frozen(source_id, expected_revocation_epoch=epoch)
        except ValueError as error:
            raise MemoryError("personal_validation_source_revoked") from error
        async with self.factory() as session:
            if await self._verify(session) != (source_id, epoch, max_risk):
                raise MemoryError("personal_validation_authorization_revoked")
