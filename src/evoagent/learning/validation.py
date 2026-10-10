"""Freeze user-supplied personal cases atomically; execution is a separate stage.

This service is a trusted local control-plane entry, never a model tool.
prepare_cases only freezes inputs; start explicitly enqueues execution.
Real execution additionally requires a frozen host profile and numeric budgets.
"""

import asyncio
from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator
from sqlalchemy import select

from evoagent.db.models import (
    ArtifactRecord,
    EvalCaseRecord,
    EvalRunRecord,
    LearningRequestRecord,
    LearningSourceRecord,
    MaintenanceJobRecord,
    SkillSourceRecord,
)
from evoagent.db.repositories.base import ConcurrentUpdateError
from evoagent.db.repositories.events import RunEventRepository
from evoagent.evals.datasets import EvalDatasetService
from evoagent.evals.lifecycle import DatasetStatus
from evoagent.evals.schema import EvalCaseDefinition, EvalDatasetDefinition, ValidatorSpec
from evoagent.learning.replicas import fixture_input_fingerprint, validation_input_fingerprint
from evoagent.learning.repository import LearningRepository
from evoagent.learning.schema import LearningError
from evoagent.learning.service import LearningService, request_view
from evoagent.learning.sources import PersonalSourceService
from evoagent.learning.validation_profiles import real_profile, require_frozen_profile
from evoagent.learning.validation_schema import ValidationSubmission
from evoagent.privacy.redaction import detect_sensitive
from evoagent.skills.access import SkillAccessPolicy, source_graph_identity
from evoagent.skills.canonical import content_hash
from evoagent.skills.lifecycle import SkillVersionStatus
from evoagent.skills.trials import TrialScope
from evoagent.tasks.lease_guard import database_now


class ValidationAdmission(ValidationSubmission):
    execution_profile_id: Literal["offline-mock-v1", "host-real-v1"] = "offline-mock-v1"
    expected_parent_lock_version: int = Field(ge=0)
    reviewed_source_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    independence_reason: str = Field(min_length=1, max_length=2000)

    @model_validator(mode="after")
    def explicit_safe_review(self):
        if not self.independence_reason.strip() or detect_sensitive(self.independence_reason):
            raise ValueError("validation requires a safe explicit source/input review")
        return self


class PersonalValidationService:
    def __init__(
        self, factory, store, validators, *, learning_enabled=False, fixtures=(), settings=None
    ):
        self.factory, self.store, self.validators = factory, store, validators
        self.enabled = learning_enabled
        self.settings = settings
        self.fixtures = {item.fixture_id: item for item in fixtures}
        if len(self.fixtures) != len(fixtures):
            raise LearningError("validation_fixture_registry_duplicate")
        for fixture in fixtures:
            fixture.manifest()

    async def start(self, request_id, expected_lock_version):
        """Explicit, source-authorized dispatch with durable replay identity."""
        if not self.enabled:
            raise LearningError("learning_disabled")
        if type(expected_lock_version) is not int or expected_lock_version < 0:
            raise LearningError("invalid_learning_request_version")
        async with self.factory() as session:
            request = await session.get(LearningRequestRecord, request_id)
            if request is None:
                raise LearningError("learning_request_not_found")
            if request.request_kind != "validate":
                raise LearningError("validation_project_replica_required")
            from evoagent.learning.project_validation import require_project_replica_contract

            require_project_replica_contract(request)
            source = await session.get(
                LearningSourceRecord, UUID(request.frozen_inputs["source_id"])
            )
            if source is None:
                raise LearningError("learning_source_revoked")
            token = source.id, source.revocation_epoch, source.content_hash
            max_risk = request.policy_snapshot["max_source_risk"]
        await PersonalSourceService(
            self.factory, artifact_store=self.store, max_source_risk=max_risk
        ).read_frozen(token[0], expected_revocation_epoch=token[1])
        async with self.factory() as session:
            control = LearningService(self.factory)
            request = await control._locked_request(session, request_id, None)
            policy = await control._policy(session, request.workspace_id)
            profile = require_frozen_profile(request.policy_snapshot, self.settings)
            if policy["mode"] == "off":
                raise LearningError("learning_policy_off")
            budget_waiting = profile is not None and any(
                policy[key] is None or policy[key] <= 0
                for key in ("daily_limit_micros", "request_limit_micros")
            )
            source = await session.get(LearningSourceRecord, token[0], populate_existing=True)
            if (
                source is None
                or source.status != "valid"
                or (source.id, source.revocation_epoch, source.content_hash) != token
                or source.content_hash != request.frozen_inputs["source_hash"]
                or source.run_id != request.origin_run_id
            ):
                raise LearningError("learning_source_revoked")
            await PersonalSourceService(
                self.factory, max_source_risk=min(max_risk, policy["max_source_risk"])
            ).check_in_session(
                session,
                request.origin_run_id,
                source.feedback_id,
                "feedback" if source.feedback_id else "manual",
            )
            current_proofs = {}
            candidate = await SkillAccessPolicy().check(
                session,
                request.candidate_version_id,
                workspace_id=request.workspace_id,
                project_id=request.project_id,
                source_proofs=current_proofs,
            )
            frozen_provenance = request.policy_snapshot.get("source_input_provenance")
            if (
                frozen_provenance is not None
                and source_graph_identity(current_proofs) != frozen_provenance
            ):
                raise LearningError("validation_source_graph_changed")
            if (
                candidate.lifecycle_status is not SkillVersionStatus.DRAFT
                or candidate.content_hash != request.frozen_inputs["candidate_content_hash"]
                or request.policy_hash != content_hash(request.policy_snapshot)
                or request.policy_hash != request.frozen_inputs["validation_policy_hash"]
                or LearningRepository.build_source_key("validate", request.frozen_inputs)
                != request.source_key
            ):
                raise LearningError("validation_execution_identity_invalid")
            dedupe = f"learning:{request.id}:task_validate:{expected_lock_version}"
            previous = await session.scalar(
                select(MaintenanceJobRecord).where(MaintenanceJobRecord.dedupe_key == dedupe)
            )
            if previous is not None:
                if (
                    previous.learning_request_id != request.id
                    or previous.kind != "learning_validate"
                ):
                    raise LearningError("validation_dispatch_identity_invalid")
                return request_view(request)
            if request.lock_version != expected_lock_version:
                raise ConcurrentUpdateError("learning_request_version_conflict")
            if request.stage != "task_validate" or request.status not in {
                "queued",
                "waiting_budget",
            }:
                raise LearningError("validation_already_dispatched_or_terminal")
            if request.status == "waiting_budget" and (
                request.error_code != "learning_waiting_budget"
                or request.validation_experiment_id is not None
            ):
                raise LearningError("validation_already_dispatched_or_terminal")
            if budget_waiting:
                if request.status != "waiting_budget":
                    request.status = "waiting_budget"
                    request.error_code = "learning_waiting_budget"
                    request.lock_version += 1
                    await control._audit(
                        session,
                        request,
                        "waiting_budget",
                        "explicit start lacks current numeric approval",
                    )
                result = request_view(request)
                await session.commit()
                return result
            if request.status == "waiting_budget":
                # Same immutable profile, cases and original spending caps. No
                # previously dispatched job is retried by this admission path.
                request.status = "queued"
                request.error_code = None
            session.add(
                MaintenanceJobRecord(
                    dedupe_key=dedupe,
                    kind="learning_validate",
                    learning_request_id=request.id,
                    payload={
                        "request_id": str(request.id),
                        "stage": "task_validate",
                        "request_lock_version": request.lock_version,
                    },
                )
            )
            await RunEventRepository(session).append(
                run_id=request.origin_run_id,
                event_type="learning.validation_dispatched",
                payload={
                    "request_id": str(request.id),
                    "actor": "local-user",
                    "provider": request.policy_snapshot["provider"],
                },
                created_at=await database_now(session),
            )
            result = request_view(request)
            await session.commit()
            return result

    def _require_meaningful_spec(self, criterion):
        spec = criterion.validator
        if spec is None:
            return
        if not self.validators.supports(spec.name, spec.version):
            raise LearningError("validation_validator_not_registered")
        if criterion.business_criterion and spec.name in {
            "artifact_exists",
            "minimum_citations",
            "contains_sections",
        }:
            raise LearningError("validation_structure_is_not_business_verification")
        parameters = spec.parameters
        text_lists = {
            "covers_items": ("items",),
            "contains_sections": ("sections",),
            "preserves_constraints": ("required", "forbidden"),
            "tool_policy": ("allowed_tools",),
        }
        if spec.name in text_lists:
            keys = text_lists[spec.name]
            if set(parameters) - set(keys) or not parameters.get(keys[0]):
                raise LearningError("validation_empty_or_unknown_parameters")
            for values in parameters.values():
                if (
                    type(values) is not list
                    or len(values) > 64
                    or any(
                        type(value) is not str or not value.strip() or len(value) > 1000
                        for value in values
                    )
                    or len(set(values)) != len(values)
                ):
                    raise LearningError("validation_parameters_invalid")
        elif spec.name in {"run_completed", "no_unknown_effects", "no_duplicate_effects"}:
            if parameters:
                raise LearningError("validation_parameters_invalid")
        elif spec.name in {"max_tool_calls", "minimum_citations"}:
            key = "maximum" if spec.name == "max_tool_calls" else "minimum"
            value = parameters.get(key)
            minimum = 0 if spec.name == "max_tool_calls" else 1
            if set(parameters) != {key} or type(value) is not int or not minimum <= value <= 64:
                raise LearningError("validation_parameters_invalid")
        elif spec.name == "expected_status":
            if (
                set(parameters) != {"status"}
                or type(parameters.get("status")) is not str
                or parameters["status"]
                not in {
                    "completed",
                    "failed",
                    "cancelled",
                }
            ):
                raise LearningError("validation_parameters_invalid")
        elif spec.name == "artifact_exists":
            if (
                set(parameters) != {"type"}
                or type(parameters["type"]) is not str
                or not 1 <= len(parameters["type"]) <= 128
            ):
                raise LearningError("validation_parameters_invalid")
        else:
            # A registered extension still needs an explicit parameter contract
            # before this service can accept user-frozen cases for it.
            raise LearningError("validation_parameter_contract_not_registered")

    async def prepare_cases(self, parent_id: UUID, payload: ValidationAdmission):
        if not self.enabled:
            raise LearningError("learning_disabled")
        profile = None
        if payload.execution_profile_id == "host-real-v1":
            if self.settings is None:
                raise LearningError("validation_model_not_registered")
            profile = real_profile(self.settings)
        control = LearningService(self.factory, learning_enabled=self.enabled)
        async with self.factory() as session:
            initial = await control._locked_request(
                session, parent_id, payload.expected_parent_lock_version
            )
            source = await session.scalar(
                select(LearningSourceRecord).where(
                    LearningSourceRecord.run_id == initial.origin_run_id,
                    LearningSourceRecord.source_revision
                    == initial.frozen_inputs.get("source_revision"),
                )
            )
            if source is None or source.status != "valid":
                raise LearningError("learning_source_revoked")
            source_id, source_epoch, source_hash = (
                source.id,
                source.revocation_epoch,
                source.content_hash,
            )
            max_risk = initial.policy_snapshot["max_source_risk"]
            lineage_proofs = {}
            await SkillAccessPolicy().check(
                session,
                initial.candidate_version_id,
                workspace_id=initial.workspace_id,
                project_id=initial.project_id,
                source_proofs=lineage_proofs,
            )
            if len(lineage_proofs) > 200:
                raise LearningError("validation_source_graph_budget_exceeded")
            formal_artifacts = [
                p.artifact_id for p in lineage_proofs.values() if p.source_kind == "train_eval"
            ]
            known_formal_inputs = set()
            if formal_artifacts:
                public_inputs = list(
                    await session.scalars(
                        select(EvalCaseRecord.public_input)
                        .join(EvalRunRecord, EvalRunRecord.eval_case_id == EvalCaseRecord.id)
                        .where(
                            EvalRunRecord.id.in_(
                                select(SkillSourceRecord.source_eval_run_id).where(
                                    SkillSourceRecord.trace_artifact_id.in_(formal_artifacts)
                                )
                            )
                        )
                        .limit(201)
                    )
                )
                if len(public_inputs) > 200:
                    raise LearningError("validation_source_graph_budget_exceeded")
                known_formal_inputs = {
                    content_hash(item["inputs"]) for item in public_inputs if item.get("inputs")
                }
        # Whole-source current-policy review happens outside a database lock;
        # admission rechecks the source identity and authorization below.
        sources = PersonalSourceService(
            self.factory, artifact_store=self.store, max_source_risk=max_risk
        )
        try:
            async with asyncio.timeout(10):
                evidence = await sources.read_frozen(
                    source_id, expected_revocation_epoch=source_epoch
                )
                lineage_input_hashes = {item.get("hash") for item in evidence.input_refs}
                for proof in lineage_proofs.values():
                    if proof.source_kind != "personal" or proof.learning_source_id == source_id:
                        continue
                    ancestor_evidence = await sources.read_frozen(
                        proof.learning_source_id,
                        expected_revocation_epoch=proof.revocation_epoch,
                    )
                    lineage_input_hashes.update(
                        item.get("hash") for item in ancestor_evidence.input_refs
                    )
        except TimeoutError as error:
            raise LearningError("validation_source_scan_budget_exceeded") from error
        if payload.reviewed_source_hash != source_hash:
            raise LearningError("validation_source_review_stale")
        fixture_manifests = {}
        for case in payload.cases:
            if case.fixture_id is not None:
                fixture = self.fixtures.get(case.fixture_id)
                if fixture is None:
                    raise LearningError("validation_fixture_not_registered")
                fixture_manifests[case.case_key] = fixture.manifest()
        for case in payload.cases:
            for item in case.criteria:
                self._require_meaningful_spec(item)
            if case.task_family in {"coding", "data", "document", "file_management"} and not any(
                item.kind == "user" and item.business_criterion for item in case.criteria
            ):
                # The current registry inspects text/runtime structure, not
                # artifact business contents or independently rerun assertions.
                raise LearningError("validation_business_judge_required")
        if sum(len(case.criteria) for case in payload.cases) * payload.repeats * 2 > 100:
            raise LearningError("validation_report_item_bound")
        input_hashes = lineage_input_hashes | known_formal_inputs
        case_fingerprints = {
            case.case_key: validation_input_fingerprint(case, fixture_manifests)
            for case in payload.cases
        }
        fixture_fingerprints = [
            fixture_input_fingerprint(item) for item in fixture_manifests.values()
        ]
        if len(set(fixture_fingerprints)) != len(fixture_fingerprints):
            raise LearningError("validation_fixture_inputs_not_distinct")
        compared_inputs = (
            set(case_fingerprints.values())
            | {
                "sha256:" + row["sha256"]
                for manifest in fixture_manifests.values()
                for row in manifest["files"]
            }
            | {
                content_hash(case.public_input["inputs"])
                for case in payload.cases
                if case.public_input.get("inputs")
            }
        )
        if compared_inputs & input_hashes:
            raise LearningError("validation_input_reuses_source")
        body = payload.model_dump(mode="json", exclude={"client_request_id"})
        if payload.execution_profile_id == "offline-mock-v1":
            body.pop("execution_profile_id")
        criteria = {
            case.case_key: [item.model_dump(mode="json") for item in case.criteria]
            for case in payload.cases
        }
        async with self.factory() as session:
            parent = await control._locked_request(
                session, parent_id, payload.expected_parent_lock_version
            )
            if (
                parent.request_kind != "propose"
                or parent.status != "completed"
                or parent.stage != "reviewed"
            ):
                raise LearningError("validation_candidate_review_required")
            if parent.project_id is not None and any(
                case.fixture_id is None for case in payload.cases
            ):
                raise LearningError("validation_project_replica_required")
            policy = await control._policy(session, parent.workspace_id)
            if policy["mode"] == "off":
                raise LearningError("learning_policy_off")
            if profile is not None and any(
                policy[key] is None or policy[key] <= 0
                for key in ("daily_limit_micros", "request_limit_micros")
            ):
                raise LearningError("learning_waiting_budget")
            source = await session.get(LearningSourceRecord, source_id, populate_existing=True)
            artifact = await session.get(ArtifactRecord, source.artifact_id) if source else None
            if (
                source is None
                or source.status != "valid"
                or source.revocation_epoch != source_epoch
                or source.content_hash != source_hash
                or artifact is None
                or artifact.attributes.get("erased")
                or artifact.redaction_status == "quarantined"
                or artifact.content_hash != source_hash
            ):
                raise LearningError("learning_source_revoked")
            sources.max_source_risk = min(max_risk, policy["max_source_risk"])
            await sources.check_in_session(
                session,
                parent.origin_run_id,
                source.feedback_id,
                "feedback" if source.feedback_id else "manual",
            )
            current_proofs = {}
            candidate = await SkillAccessPolicy().check(
                session,
                parent.candidate_version_id,
                workspace_id=parent.workspace_id,
                project_id=parent.project_id,
                source_proofs=current_proofs,
            )
            if current_proofs != lineage_proofs:
                raise LearningError("validation_source_graph_changed")
            if candidate.lifecycle_status is not SkillVersionStatus.DRAFT:
                raise LearningError("validation_candidate_not_draft")
            if (
                parent.validation_report is None
                or content_hash(parent.validation_report) != parent.validation_report_hash
                or parent.validation_report.get("static_validation", {}).get("passed") is not True
            ):
                raise LearningError("validation_static_evidence_required")
            policy_snapshot = {
                **policy,
                "max_source_risk": sources.max_source_risk,
                "validation_mode": "personal_validation",
                "selection_contract_version": 3,
                "provider": "mock",
                "model": "mock",
                "repeats": payload.repeats,
                "input_review": {
                    "origin": "user",
                    "actor": "local-user",
                    "source_hash": source_hash,
                    "reason": payload.independence_reason,
                },
                "independence_scope": "user_review_plus_exact_known_input_hashes",
                "source_input_provenance": source_graph_identity(lineage_proofs),
            }
            if parent.project_id is not None:
                policy_snapshot.update(
                    project_replica_policy="registered-fixtures:v1",
                    source_project_id=str(parent.project_id),
                )
            if profile is not None:
                identity = profile.identity()
                policy_snapshot.update(
                    provider=profile.provider,
                    model=profile.model,
                    execution_profile=identity["profile"],
                    execution_profile_hash=identity["profile_hash"],
                    maximum_model_calls=len(payload.cases)
                    * payload.repeats
                    * 2
                    * profile.max_iterations,
                )
            definition = EvalDatasetDefinition(
                purpose="personal_dev",
                name="pv_" + content_hash({"parent_id": str(parent.id), "body": body})[7:39],
                version=1,
                cases=tuple(
                    EvalCaseDefinition(
                        case_key=case.case_key,
                        task_family=case.task_family,
                        split="train",
                        public_input=case.public_input,
                        private_validators=tuple(
                            item.validator for item in case.criteria if item.validator is not None
                        )
                        or (ValidatorSpec(name="run_completed"),),
                        risk_profile={
                            "case_kind": case.case_kind,
                            "input_fingerprint": case_fingerprints[case.case_key],
                        },
                    )
                    for case in payload.cases
                ),
            )
            dataset = await EvalDatasetService(self.factory).import_in_session(session, definition)
            dataset.status = DatasetStatus.FROZEN
            frozen = {
                **parent.frozen_inputs,
                "parent_request_id": str(parent.id),
                "candidate_version_id": str(candidate.id),
                "candidate_content_hash": candidate.content_hash,
                "validation_input_manifest_hash": dataset.content_hash,
                "validation_criteria_hash": content_hash(criteria),
                "validation_policy_hash": content_hash(policy_snapshot),
                "validator_version": "personal:v1",
                "target_scope_key": TrialScope(parent.workspace_id, parent.project_id).key,
                "validation_dataset_id": str(dataset.id),
                "validation_cases": [case.model_dump(mode="json") for case in payload.cases],
                "input_fingerprints": case_fingerprints,
                "source_id": str(source_id),
                "source_hash": source_hash,
            }
            if fixture_manifests:
                frozen["fixture_manifests"] = fixture_manifests
                frozen["fixture_manifest_hash"] = content_hash(fixture_manifests)
                # Fixture identity must participate in validate:v1 deduplication.
                policy_snapshot["fixture_manifest_hash"] = frozen["fixture_manifest_hash"]
                frozen["validation_policy_hash"] = content_hash(policy_snapshot)
            row = await LearningRepository(session).append_request(
                workspace_id=parent.workspace_id,
                project_id=parent.project_id,
                origin_run_id=parent.origin_run_id,
                client_request_id=payload.client_request_id,
                kind="validate",
                frozen_inputs=frozen,
                policy_snapshot=policy_snapshot,
                request_body=body,
                parent_request_id=parent.id,
                target_skill_id=candidate.skill_id,
                base_version_id=parent.base_version_id,
            )
            if row.candidate_version_id is None:
                row.candidate_version_id, row.stage = candidate.id, "task_validate"
                await RunEventRepository(session).append(
                    run_id=parent.origin_run_id,
                    event_type="learning.validation_inputs_frozen",
                    payload={
                        "request_id": str(row.id),
                        "dataset_id": str(dataset.id),
                        "criteria_hash": frozen["validation_criteria_hash"],
                        "review_origin": "user",
                        "execution_started": False,
                    },
                    created_at=await database_now(session),
                )
            elif row.candidate_version_id != candidate.id:
                raise ConcurrentUpdateError("validation_candidate_conflict")
            result = request_view(row)
            await session.commit()
            return result
