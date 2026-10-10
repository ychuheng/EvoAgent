"""Trusted human control plane; never accept model verdicts as user authority."""

from copy import deepcopy
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator
from sqlalchemy import select

from evoagent.db.models import (
    EvalExperimentRecord,
    EvalRunRecord,
    LearningRequestRecord,
    LearningSourceRecord,
    ValidationJudgmentRecord,
)
from evoagent.db.repositories.base import ConcurrentUpdateError
from evoagent.db.repositories.events import RunEventRepository
from evoagent.learning.repository import LearningRepository
from evoagent.learning.schema import LearningError
from evoagent.learning.service import LearningService
from evoagent.learning.sources import PersonalSourceService
from evoagent.privacy.redaction import detect_sensitive, redact_value
from evoagent.skills.access import SkillAccessPolicy
from evoagent.skills.canonical import canonical_json, content_hash
from evoagent.skills.lifecycle import SkillVersionStatus
from evoagent.tasks.lease_guard import database_now


class HumanCriterionJudgment(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    eval_run_id: UUID
    criterion_id: str = Field(pattern=r"^[a-z][a-z0-9_]{1,63}$")
    verdict: Literal["pass", "fail", "unknown"]
    observed: JsonValue
    reason: str = Field(min_length=1, max_length=2000)

    @model_validator(mode="after")
    def require_safe_evidence(self):
        if (
            not self.reason.strip()
            or len(canonical_json(self.model_dump(mode="json")).encode()) > 8192
            or detect_sensitive(canonical_json(self.model_dump(mode="json")))
            or redact_value(self.observed) != self.observed
        ):
            raise ValueError("human judgment requires bounded safe evidence")
        return self


class HumanJudgmentSubmission(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    client_request_id: str = Field(min_length=1, max_length=128)
    expected_lock_version: int = Field(ge=0)
    expected_report_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    judgments: tuple[HumanCriterionJudgment, ...] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def unique_items(self):
        keys = [(item.eval_run_id, item.criterion_id) for item in self.judgments]
        if len(set(keys)) != len(keys):
            raise ValueError("duplicate human criterion judgment")
        if len(canonical_json(self.model_dump(mode="json")).encode()) > 128 * 1024:
            raise ValueError("human judgment batch exceeds bound")
        return self


class ValidationJudgmentService:
    def __init__(self, factory, store, *, learning_enabled=False):
        self.factory, self.store, self.enabled = factory, store, learning_enabled

    async def _binding(self, session, request):
        if (
            request is None
            or request.request_kind != "validate"
            or request.status != "ready_for_review"
            or request.stage != "validation_review"
            or not isinstance(request.validation_report, dict)
            or content_hash(request.validation_report) != request.validation_report_hash
            or content_hash(request.policy_snapshot) != request.policy_hash
            or request.policy_hash != request.frozen_inputs.get("validation_policy_hash")
            or LearningRepository.build_source_key("validate", request.frozen_inputs)
            != request.source_key
        ):
            raise LearningError("validation_not_ready_for_human_review")
        from evoagent.learning.project_validation import require_project_replica_contract

        require_project_replica_contract(request)
        policy = await LearningService(self.factory)._policy(session, request.workspace_id)
        if policy["mode"] == "off":
            raise LearningError("learning_policy_off")
        source = await session.get(LearningSourceRecord, UUID(request.frozen_inputs["source_id"]))
        if (
            source is None
            or source.status != "valid"
            or source.run_id != request.origin_run_id
            or source.content_hash != request.frozen_inputs["source_hash"]
        ):
            raise LearningError("learning_source_revoked")
        candidate = await SkillAccessPolicy().check(
            session,
            request.candidate_version_id,
            workspace_id=request.workspace_id,
            project_id=request.project_id,
        )
        if (
            candidate.lifecycle_status is not SkillVersionStatus.DRAFT
            or candidate.content_hash != request.frozen_inputs["candidate_content_hash"]
        ):
            raise LearningError("validation_candidate_unavailable")
        report = request.validation_report
        experiment = await session.get(EvalExperimentRecord, request.validation_experiment_id)
        if (
            experiment is None
            or str(experiment.status) != "completed"
            or experiment.purpose != "personal_validation"
            or experiment.learning_request_id != request.id
            or experiment.skill_version_id != candidate.id
            or experiment.report_hash != report.get("execution_report_hash")
            or str(experiment.report_artifact_id) != report.get("execution_report_artifact_id")
            or report.get("candidate_hash") != candidate.content_hash
            or report.get("input_manifest_hash")
            != request.frozen_inputs["validation_input_manifest_hash"]
            or report.get("criteria_hash") != request.frozen_inputs["validation_criteria_hash"]
        ):
            raise LearningError("validation_execution_binding_invalid")
        if request.policy_snapshot.get("execution_profile") is not None:
            from evoagent.learning.validation_evidence import verify_report_cost

            await verify_report_cost(session, request, report)
        return (
            source.id,
            source.revocation_epoch,
            min(policy["max_source_risk"], request.policy_snapshot["max_source_risk"]),
        )

    @staticmethod
    def view(record):
        return {
            "id": record.id,
            "request_id": record.request_id,
            "revision": record.revision,
            "actor": record.actor,
            "report_hash": record.resulting_report_hash,
            "business_verification": record.resulting_report["business_verification"],
            "trial_eligible": record.resulting_report["trial_eligible"],
        }

    async def submit(self, request_id, payload: HumanJudgmentSubmission):
        if not self.enabled:
            raise LearningError("learning_disabled")
        body_hash = content_hash(payload.model_dump(mode="json", exclude={"client_request_id"}))
        async with self.factory() as session:
            request = await session.get(LearningRequestRecord, request_id)
            if request is None:
                raise LearningError("learning_request_not_found")
            existing = await session.scalar(
                select(ValidationJudgmentRecord).where(
                    ValidationJudgmentRecord.workspace_id == request.workspace_id,
                    ValidationJudgmentRecord.client_request_id == payload.client_request_id,
                )
            )
            if existing is not None:
                if existing.request_id != request_id or existing.request_body_hash != body_hash:
                    raise ConcurrentUpdateError("validation_judgment_client_conflict")
                return self.view(existing)
            token = await self._binding(session, request)
        source_id, epoch, risk = token
        await PersonalSourceService(
            self.factory,
            artifact_store=self.store,
            max_source_risk=risk,
        ).read_frozen(source_id, expected_revocation_epoch=epoch)
        async with self.factory() as session:
            request = await LearningService(self.factory)._locked_request(session, request_id, None)
            existing = await session.scalar(
                select(ValidationJudgmentRecord).where(
                    ValidationJudgmentRecord.workspace_id == request.workspace_id,
                    ValidationJudgmentRecord.client_request_id == payload.client_request_id,
                )
            )
            if existing is not None:
                if existing.request_id != request_id or existing.request_body_hash != body_hash:
                    raise ConcurrentUpdateError("validation_judgment_client_conflict")
                return self.view(existing)
            if (
                request.lock_version != payload.expected_lock_version
                or request.validation_report_hash != payload.expected_report_hash
            ):
                raise ConcurrentUpdateError("validation_judgment_report_conflict")
            if await self._binding(session, request) != token:
                raise LearningError("learning_source_changed")
            report = deepcopy(request.validation_report)
            index = {}
            for item in report["items"]:
                refs = [ref["id"] for ref in item["evidence_refs"] if ref["type"] == "eval_run"]
                if len(refs) != 1:
                    raise LearningError("validation_execution_binding_invalid")
                key = UUID(refs[0]), item["criterion_id"]
                if key in index:
                    raise LearningError("validation_execution_binding_invalid")
                index[key] = item
            identifier = uuid4()
            for judgment in payload.judgments:
                item = index.get((judgment.eval_run_id, judgment.criterion_id))
                evaluation = await session.get(EvalRunRecord, judgment.eval_run_id)
                if (
                    item is None
                    or item["judge_origin"] != "user"
                    or evaluation is None
                    or evaluation.experiment_id != request.validation_experiment_id
                    or evaluation.arm != item["arm"]
                    or evaluation.repeat_index != item["repeat"]
                    or {"type": "run", "id": str(evaluation.run_id)} not in item["evidence_refs"]
                ):
                    raise LearningError("validation_human_judge_binding_invalid")
                item.update(
                    verdict=judgment.verdict,
                    observed=judgment.observed,
                    judge_id="local-user",
                    judgment_id=str(identifier),
                    judgment_reason=judgment.reason,
                )
            business = [item for item in report["items"] if item["business_criterion"]]
            report["business_verification"] = (
                "failed"
                if any(item["verdict"] == "fail" for item in business)
                else "passed"
                if business and all(item["verdict"] == "pass" for item in business)
                else "pending"
            )
            # Human agreement with Mock runs or unknown costs cannot grant trial.
            from evoagent.learning.validation_profiles import real_trial_eligible

            report["trial_eligible"] = real_trial_eligible(report, request.policy_snapshot)
            report["latest_judgment_id"] = str(identifier)
            if len(canonical_json(report).encode()) > 128 * 1024 or detect_sensitive(
                canonical_json(report)
            ):
                raise LearningError("validation_report_unsafe_or_oversized")
            record = ValidationJudgmentRecord(
                id=identifier,
                workspace_id=request.workspace_id,
                request_id=request.id,
                revision=request.lock_version + 1,
                client_request_id=payload.client_request_id,
                request_body_hash=body_hash,
                actor="local-user",
                base_report_hash=request.validation_report_hash,
                judgments=[item.model_dump(mode="json") for item in payload.judgments],
                resulting_report=report,
                resulting_report_hash=content_hash(report),
            )
            session.add(record)
            request.validation_report, request.validation_report_hash = (
                report,
                record.resulting_report_hash,
            )
            request.lock_version += 1
            await RunEventRepository(session).append(
                run_id=request.origin_run_id,
                event_type="learning.validation_human_judged",
                payload={
                    "request_id": str(request.id),
                    "judgment_id": str(identifier),
                    "report_hash": record.resulting_report_hash,
                    "actor": "local-user",
                },
                created_at=await database_now(session),
            )
            await session.commit()
            return self.view(record)
