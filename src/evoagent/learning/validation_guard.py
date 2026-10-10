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
from evoagent.learning.replica_bindings import replica_factory, require_replica_binding
from evoagent.learning.replicas import validation_input_fingerprint
from evoagent.learning.schema import LearningError
from evoagent.learning.sources import PersonalSourceService
from evoagent.learning.validation_profiles import require_frozen_profile
from evoagent.learning.validation_schema import PersonalValidationCase
from evoagent.memory.schema import MemoryError
from evoagent.skills.access import SkillAccessError, SkillAccessPolicy, source_graph_identity
from evoagent.skills.canonical import content_hash
from evoagent.skills.lifecycle import SkillVersionStatus


class PersonalValidationRunGuard:
    def __init__(
        self, factory, store, run_id, evaluation_id, request_id, *, learning_enabled, settings=None
    ):
        self.factory, self.store = factory, store
        self.run_id, self.evaluation_id, self.request_id = run_id, evaluation_id, request_id
        self.enabled = learning_enabled
        self.settings = settings

    @classmethod
    async def for_run(cls, factory, store, run_id, *, learning_enabled, settings=None):
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
            factory,
            store,
            run_id,
            rows[0][0],
            rows[0][1],
            learning_enabled=learning_enabled,
            settings=settings,
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
            or run.provider != request.policy_snapshot.get("provider")
            or run.model != request.policy_snapshot.get("model")
            or content_hash(request.policy_snapshot) != request.policy_hash
            or request.policy_hash != request.frozen_inputs.get("validation_policy_hash")
            or experiment.config_snapshot.get("selection_contract_version")
            != request.policy_snapshot.get("selection_contract_version")
            or request.policy_snapshot.get("selection_contract_version") == 3
            and run.config_snapshot is not None
            and run.config_snapshot.get("schema_version") != 3
        ):
            raise MemoryError("personal_validation_authorization_revoked")
        try:
            from evoagent.learning.project_validation import require_project_replica_contract

            require_project_replica_contract(request)
            require_frozen_profile(request.policy_snapshot, self.settings)
        except LearningError as error:
            raise MemoryError(error.code) from None
        try:
            frozen_case = next(
                PersonalValidationCase.model_validate(item)
                for item in request.frozen_inputs["validation_cases"]
                if item["case_key"] == case.case_key
            )
            source_id = UUID(request.frozen_inputs["source_id"])
        except (KeyError, ValueError, TypeError, StopIteration):
            # Parser errors may retain the full legacy input in their context.
            raise MemoryError("personal_validation_frozen_input_invalid") from None
        try:
            fingerprint = validation_input_fingerprint(
                frozen_case, request.frozen_inputs.get("fixture_manifests", {})
            )
        except ValueError:
            raise MemoryError("personal_validation_frozen_input_invalid") from None
        if (
            frozen_case.public_input != case.public_input
            or frozen_case.task_family != case.task_family
            or request.policy_snapshot.get("selection_contract_version") == 3
            and task.family != frozen_case.task_family
            or fingerprint != request.frozen_inputs.get("input_fingerprints", {}).get(case.case_key)
        ):
            raise MemoryError("personal_validation_frozen_input_invalid")
        try:
            binding = await require_replica_binding(session, request, evaluation, frozen_case)
            if binding is not None:
                await replica_factory(self.store).resume(
                    request.id,
                    case.case_key,
                    evaluation.arm,
                    evaluation.repeat_index,
                    expected_manifest=binding.manifest,
                    verify_initial_inputs=run.config_snapshot is None,
                )
        except ValueError:
            raise MemoryError("personal_validation_replica_unavailable") from None
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
            lineage_proofs = {}
            candidate = await SkillAccessPolicy().check(
                session,
                request.candidate_version_id,
                workspace_id=request.workspace_id,
                project_id=request.project_id,
                source_proofs=lineage_proofs,
            )
            expected = request.policy_snapshot.get("source_input_provenance")
            if expected is not None and source_graph_identity(lineage_proofs) != expected:
                raise SkillAccessError("validation_source_graph_changed")
            if version_id is not None and version_id != request.candidate_version_id:
                comparison = await SkillAccessPolicy().check(
                    session,
                    version_id,
                    workspace_id=request.workspace_id,
                    project_id=request.project_id,
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
        except ValueError:
            raise MemoryError("personal_validation_source_revoked") from None
        async with self.factory() as session:
            if await self._verify(session) != (source_id, epoch, max_risk):
                raise MemoryError("personal_validation_authorization_revoked")

    async def selection_context(self):
        """Host facts from the immutable initial replica, never from edited outputs."""
        from evoagent.db.models import ValidationReplicaBindingRecord
        from evoagent.projects.inputs import InputSet
        from evoagent.skills.selection_snapshot import SkillSelectionScope

        await self.check()
        async with self.factory() as session:
            await self._verify(session)
            request = await session.get(LearningRequestRecord, self.request_id)
            if request.policy_snapshot.get("selection_contract_version") is None:
                return None
            if request.policy_snapshot.get("selection_contract_version") != 3:
                raise MemoryError("personal_validation_selection_contract_invalid")
            binding = await session.scalar(
                select(ValidationReplicaBindingRecord).where(
                    ValidationReplicaBindingRecord.run_id == self.run_id
                )
            )
            inputs = None
            if binding is not None:
                try:
                    inputs = InputSet.model_validate(
                        {"files": [{**row, "kind": "fixture"} for row in binding.manifest["files"]]}
                    ).model_dump(mode="json")
                except ValueError:
                    raise MemoryError("personal_validation_replica_unavailable") from None
            return SkillSelectionScope(
                workspace_id=request.workspace_id, project_id=request.project_id
            ), inputs

    async def execution_project(self):
        """An execution root grants no access to the source user's project."""
        from evoagent.db.models import ValidationReplicaBindingRecord
        from evoagent.projects.schema import ProjectAuthorization
        from evoagent.projects.service import ActiveProject

        await self.check()
        async with self.factory() as session:
            binding = await session.scalar(
                select(ValidationReplicaBindingRecord).where(
                    ValidationReplicaBindingRecord.run_id == self.run_id
                )
            )
        if binding is None:
            return None
        replica = await replica_factory(self.store).resume(
            binding.request_id,
            binding.case_key,
            binding.arm,
            binding.repeat_index,
            expected_manifest=binding.manifest,
        )
        return ActiveProject(
            id=binding.id,
            name="Private validation replica",
            root=replica.root,
            authorization=ProjectAuthorization.READ_WRITE,
            authorization_version=1,
        )
