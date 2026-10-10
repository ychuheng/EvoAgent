"""Fenced personal validation with explicit host profiles and private replicas.

Mock execution remains offline and cannot grant trial adoption. Paid execution
requires frozen host registration and task-bound learning budget reservations.
"""

import json
from uuid import UUID

from sqlalchemy import select

from evoagent.db.models import (
    EvalCaseRecord,
    EvalDatasetRecord,
    EvalExperimentRecord,
    EvalRunRecord,
    RunRecord,
    SessionRecord,
    TaskRecord,
)
from evoagent.evals.coordinator import EvalCoordinator
from evoagent.evals.lifecycle import EvalExperimentStatus
from evoagent.evals.metrics import EvaluationReportService
from evoagent.evals.validators.base import ValidationResult
from evoagent.learning.replica_bindings import prepare_replicas, require_replica_binding
from evoagent.learning.replicas import validation_input_fingerprint
from evoagent.learning.repository import LearningRepository
from evoagent.learning.schema import LearningError
from evoagent.learning.selection_evidence import actual_selection_evidence, adoption_contract_passed
from evoagent.learning.sources import PersonalSourceService
from evoagent.learning.validation_profiles import real_trial_eligible, require_frozen_profile
from evoagent.learning.validation_schema import PersonalValidationCase
from evoagent.privacy.redaction import detect_sensitive
from evoagent.skills.access import SkillAccessError, SkillAccessPolicy
from evoagent.skills.canonical import canonical_json, content_hash
from evoagent.skills.lifecycle import SkillVersionStatus
from evoagent.trace.artifacts import ArtifactService


class PersonalValidationExecution:
    def __init__(self, factory, store, validators, check_request, *, settings=None):
        self.factory, self.store, self.validators = factory, store, validators
        self.check_request = check_request
        self.settings = settings

    async def _load(self, session, request_id, guard):
        _, request, source = await self.check_request(session, guard, source_required=True)
        if request.id != request_id or request.request_kind != "validate":
            raise LearningError("validation_execution_identity_invalid")
        from evoagent.learning.project_validation import require_project_replica_contract

        require_project_replica_contract(request)
        frozen = request.frozen_inputs
        if (
            content_hash(request.policy_snapshot) != request.policy_hash
            or request.policy_hash != frozen.get("validation_policy_hash")
            or frozen.get("validator_version") != "personal:v1"
        ):
            raise LearningError("validation_frozen_contract_invalid")
        review = request.policy_snapshot.get("input_review")
        if (
            not isinstance(review, dict)
            or review.get("origin") != "user"
            or review.get("actor") != "local-user"
            or review.get("source_hash") != source.content_hash
        ):
            raise LearningError("validation_source_review_required")
        try:
            candidate = await SkillAccessPolicy().check(
                session,
                request.candidate_version_id,
                workspace_id=request.workspace_id,
                project_id=request.project_id,
            )
        except SkillAccessError as error:
            raise LearningError("validation_candidate_unavailable") from error
        if (
            candidate.lifecycle_status is not SkillVersionStatus.DRAFT
            or candidate.content_hash != frozen.get("candidate_content_hash")
        ):
            raise LearningError("validation_candidate_unavailable")
        if LearningRepository.build_source_key("validate", frozen) != request.source_key:
            raise LearningError("validation_execution_identity_invalid")
        profile = require_frozen_profile(request.policy_snapshot, self.settings)
        if (
            frozen.get("source_id") != str(source.id)
            or frozen.get("source_hash") != source.content_hash
        ):
            raise LearningError("validation_source_identity_invalid")
        try:
            cases = tuple(
                PersonalValidationCase.model_validate(item) for item in frozen["validation_cases"]
            )
            dataset_id = UUID(frozen["validation_dataset_id"])
            if profile is not None and request.policy_snapshot.get("maximum_model_calls") != (
                len(cases) * request.policy_snapshot["repeats"] * 2 * profile.max_iterations
            ):
                raise LearningError("validation_call_bound_invalid")
        except (KeyError, ValueError, TypeError):
            raise LearningError("validation_frozen_contract_invalid") from None
        criteria = {
            case.case_key: [item.model_dump(mode="json") for item in case.criteria]
            for case in cases
        }
        dataset = await session.get(EvalDatasetRecord, dataset_id)
        if (
            dataset is None
            or dataset.purpose != "personal_dev"
            or str(dataset.status) != "frozen"
            or dataset.content_hash != frozen["validation_input_manifest_hash"]
            or content_hash(criteria) != frozen["validation_criteria_hash"]
        ):
            raise LearningError("validation_frozen_contract_invalid")
        rows = {
            row.case_key: row
            for row in await session.scalars(
                select(EvalCaseRecord).where(EvalCaseRecord.dataset_id == dataset.id)
            )
        }
        if len(rows) != len(cases) or set(rows) != {case.case_key for case in cases}:
            raise LearningError("validation_case_identity_invalid")
        for case in cases:
            row = rows[case.case_key]
            specs = [
                item.validator.model_dump(mode="json")
                for item in case.criteria
                if item.validator is not None
            ]
            if (
                str(row.split) != "train"
                or row.task_family != case.task_family
                or row.public_input != case.public_input
                or validation_input_fingerprint(case, frozen.get("fixture_manifests", {}))
                != frozen.get("input_fingerprints", {}).get(case.case_key)
                or row.private_validators
                != (specs or [{"name": "run_completed", "version": "1", "parameters": {}}])
            ):
                raise LearningError("validation_case_identity_invalid")
        return request, source, cases, rows

    async def start(self, request_id, guard, complete_stage, *, code_version):
        async with self.factory() as session:
            request, source, cases, _ = await self._load(session, request_id, guard)
            if request.stage != "task_validate":
                raise LearningError("invalid_learning_stage")
            source_id, epoch = source.id, source.revocation_epoch
            max_risk = request.policy_snapshot["max_source_risk"]
        await PersonalSourceService(
            self.factory, artifact_store=self.store, max_source_risk=max_risk
        ).read_frozen(source_id, expected_revocation_epoch=epoch)
        replicas = await prepare_replicas(self.store, request, cases)

        async def complete(session, request, experiment):
            await self._load(session, request.id, guard)
            await complete_stage(
                session, guard, request, "waiting_validation", experiment_id=str(experiment.id)
            )

        return await EvalCoordinator(self.factory, self.validators).create_personal_validation(
            learning_request_id=request.id,
            dataset_id=UUID(request.frozen_inputs["validation_dataset_id"]),
            candidate_version_id=request.candidate_version_id,
            comparison_version_id=request.base_version_id,
            provider=request.policy_snapshot["provider"],
            model=request.policy_snapshot["model"],
            repeats=request.policy_snapshot["repeats"],
            code_version=code_version,
            job_guard=guard,
            complete_stage=complete,
            replicas=replicas,
            execution_profile=require_frozen_profile(request.policy_snapshot, self.settings),
        )

    async def collect(self, request_id, guard, complete_stage):
        async with self.factory() as session:
            request, source, cases, stored = await self._load(session, request_id, guard)
            experiment = await session.get(EvalExperimentRecord, request.validation_experiment_id)
            if (
                experiment is None
                or experiment.purpose != "personal_validation"
                or experiment.status is not EvalExperimentStatus.COMPLETED
                or experiment.learning_request_id != request.id
                or experiment.skill_version_id != request.candidate_version_id
                or experiment.comparison_version_id != request.base_version_id
                or experiment.dataset_id != UUID(request.frozen_inputs["validation_dataset_id"])
            ):
                raise LearningError("validation_execution_not_completed")
            eval_runs = list(
                await session.scalars(
                    select(EvalRunRecord).where(EvalRunRecord.experiment_id == experiment.id)
                )
            )
            expected = {
                (stored[case.case_key].id, arm, repeat)
                for case in cases
                for arm in ("control", "treatment")
                for repeat in range(request.policy_snapshot["repeats"])
            }
            if {
                (row.eval_case_id, row.arm, row.repeat_index) for row in eval_runs
            } != expected or len(eval_runs) != len(expected):
                raise LearningError("validation_execution_pairs_incomplete")
            by_case = {stored[case.case_key].id: case for case in cases}
            items = []
            for row in sorted(
                eval_runs,
                key=lambda row: (by_case[row.eval_case_id].case_key, row.arm, row.repeat_index),
            ):
                run = await session.get(RunRecord, row.run_id)
                task = await session.get(TaskRecord, row.task_id)
                chat = await session.get(SessionRecord, task.session_id) if task else None
                case = by_case[row.eval_case_id]
                await require_replica_binding(session, request, row, case)
                version_id = (
                    request.candidate_version_id
                    if row.arm == "treatment"
                    else request.base_version_id
                )
                if (
                    run is None
                    or task is None
                    or chat is None
                    or chat.workspace_id != request.workspace_id
                    or run.task_id != task.id
                    or task.project_id is not None
                    or run.data_role != "dev"
                    or row.skill_version_id != version_id
                    or run.pinned_skill_version_id != version_id
                    or run.provider != request.policy_snapshot["provider"]
                    or run.model != request.policy_snapshot["model"]
                    or task.goal
                    != json.dumps(case.public_input, ensure_ascii=False, sort_keys=True)
                    or str(run.status) not in {"completed", "failed", "cancelled"}
                    or run.started_at is None
                    or run.ended_at is None
                    or row.metrics.get("state") != "completed"
                ):
                    raise LearningError("validation_execution_binding_invalid")
                machine = [item for item in case.criteria if item.validator is not None]
                selection_evidence = None
                if request.policy_snapshot.get("selection_contract_version") == 3:
                    selection_evidence = await actual_selection_evidence(
                        session,
                        run,
                        workspace_id=request.workspace_id,
                        project_id=request.project_id,
                        target_id=version_id,
                    )
                try:
                    results = [
                        ValidationResult.model_validate(item) for item in row.validation_results
                    ]
                except ValueError:
                    raise LearningError("validation_machine_results_invalid") from None
                if len(results) != (len(machine) or 1):
                    raise LearningError("validation_machine_results_invalid")
                if not machine and (results[0].validator, results[0].version) != (
                    "run_completed",
                    "1",
                ):
                    raise LearningError("validation_machine_results_invalid")
                by_criterion = {}
                for criterion, result in zip(machine, results, strict=bool(machine)):
                    if (criterion.validator.name, criterion.validator.version) != (
                        result.validator,
                        result.version,
                    ):
                        raise LearningError("validation_machine_results_invalid")
                    by_criterion[criterion.criterion_id] = result
                for criterion in case.criteria:
                    result = by_criterion.get(criterion.criterion_id)
                    items.append(
                        {
                            "case_key": case.case_key,
                            "case_kind": case.case_kind,
                            "arm": row.arm,
                            "repeat": row.repeat_index,
                            "criterion_id": criterion.criterion_id,
                            "description": criterion.description,
                            "expected": criterion.expected,
                            "observed": result.evidence if result else None,
                            "verdict": ("pass" if result.passed else "fail")
                            if result
                            else "unknown",
                            "judge_origin": criterion.kind,
                            "judge_id": f"{result.validator}@{result.version}" if result else None,
                            "evidence_refs": [
                                {"type": "run", "id": str(run.id)},
                                {"type": "eval_run", "id": str(row.id)},
                            ],
                            "input_fingerprint": request.frozen_inputs["input_fingerprints"][
                                case.case_key
                            ],
                            "independent_input": True,
                            "independence_origin": "user_review",
                            "business_criterion": criterion.business_criterion,
                            **(
                                {"actual_selection": selection_evidence}
                                if selection_evidence is not None
                                else {}
                            ),
                        }
                    )
            experiment_id = experiment.id
            source_id, epoch, max_risk = (
                source.id,
                source.revocation_epoch,
                request.policy_snapshot["max_source_risk"],
            )
        await PersonalSourceService(
            self.factory, artifact_store=self.store, max_source_risk=max_risk
        ).read_frozen(source_id, expected_revocation_epoch=epoch)
        _, execution_hash, artifact_id = await EvaluationReportService(self.factory).freeze(
            experiment_id, ArtifactService(self.store, self.factory)
        )
        report = {
            "schema_version": 2
            if request.policy_snapshot.get("selection_contract_version") == 3
            else 1,
            "validation_mode": "personal_validation",
            "candidate_version_id": str(request.candidate_version_id),
            "candidate_hash": request.frozen_inputs["candidate_content_hash"],
            "input_manifest_hash": request.frozen_inputs["validation_input_manifest_hash"],
            "criteria_hash": request.frozen_inputs["validation_criteria_hash"],
            "execution_report_hash": execution_hash,
            "execution_report_artifact_id": str(artifact_id),
            "items": items,
            "business_verification": "pending",
            "trial_eligible": False,
            "cost": {"provider": "mock", "paid_calls": 0},
            "independence_review": request.policy_snapshot["input_review"],
        }
        if report["schema_version"] == 2:
            report["adoption_verification"] = (
                "passed"
                if adoption_contract_passed(items, request.candidate_version_id)
                else "failed"
            )
        if request.policy_snapshot.get("execution_profile") is not None:
            async with self.factory() as session:
                from evoagent.learning.validation_evidence import validation_cost

                report["cost"] = await validation_cost(session, request)
            report["execution_profile_hash"] = request.policy_snapshot["execution_profile_hash"]
            business = [item for item in items if item["business_criterion"]]
            report["business_verification"] = (
                "failed"
                if any(item["verdict"] == "fail" for item in business)
                else "passed"
                if business and all(item["verdict"] == "pass" for item in business)
                else "pending"
            )
            report["trial_eligible"] = real_trial_eligible(report, request.policy_snapshot)
        if len(canonical_json(report).encode()) > 128 * 1024 or detect_sensitive(
            canonical_json(report)
        ):
            raise LearningError("validation_report_unsafe_or_oversized")
        async with self.factory() as session:
            current, _, _, _ = await self._load(session, request_id, guard)
            current.validation_report, current.validation_report_hash = report, content_hash(report)
            await complete_stage(
                session,
                guard,
                current,
                "validation_review",
                report_hash=current.validation_report_hash,
            )
            await session.commit()
        return report
