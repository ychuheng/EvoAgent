"""Skill 来源资格检查、结构化清洗和不可变 Trace 冻结。"""

import hashlib
import json
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from evoagent.core.models import ToolRisk
from evoagent.db.models import (
    ApprovalStatus,
    ArtifactRecord,
    EvalCaseRecord,
    EvalDatasetRecord,
    EvalExperimentRecord,
    EvalRunRecord,
    ToolApprovalRecord,
    ToolCallRecord,
    ToolCallStatus,
    ToolEffectRecord,
    ToolEffectStatus,
)
from evoagent.db.unit_of_work import UnitOfWork
from evoagent.evals.lifecycle import EvalSplit
from evoagent.runtime.run_config import RunConfigSnapshot
from evoagent.skills.sanitizer import SanitizedTrace, TraceSanitizer
from evoagent.tasks.state_machine import PersistentRunStatus
from evoagent.trace.artifacts import ArtifactService
from evoagent.trace.bundle import TraceBundleService


class IneligibleSkillSourceError(ValueError):
    """EvalRun 不符合训练来源的硬性条件。"""


@dataclass(frozen=True, slots=True)
class FrozenSkillSource:
    eval_run_id: UUID | None
    run_id: UUID
    artifact_id: UUID
    source_trace_hash: str
    payload: dict[str, object]
    source_kind: str = "train_eval"
    learning_source_id: UUID | None = None


class TraceEligibilityChecker:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        allowed_tools: frozenset[str] = frozenset(
            {"calculator", "file_read", "web_fetch", "web_search", "artifact_write"}
        ),
        max_risk: ToolRisk = ToolRisk.R1,
    ) -> None:
        self._session_factory = session_factory
        self._allowed_tools = allowed_tools
        self._max_risk = max_risk

    async def check(self, eval_run_id: UUID) -> EvalRunRecord:
        async with UnitOfWork(self._session_factory) as unit:
            eval_run = await unit.evals.get_run(eval_run_id)
            case = await unit.session.get(EvalCaseRecord, eval_run.eval_case_id)
            run = await unit.runs.get(eval_run.run_id)
            experiment = await unit.session.get(EvalExperimentRecord, eval_run.experiment_id)
            dataset = await unit.session.get(EvalDatasetRecord, case.dataset_id) if case else None
            if (
                experiment is None
                or experiment.purpose != "formal"
                or dataset is None
                or dataset.purpose != "formal"
            ):
                raise IneligibleSkillSourceError(
                    "personal validation is development material, not a formal TRAIN source"
                )
            if case is None or case.split is not EvalSplit.TRAIN:
                raise IneligibleSkillSourceError("only TRAIN eval runs may become sources")
            if not eval_run.passed or run.status is not PersistentRunStatus.COMPLETED:
                raise IneligibleSkillSourceError("source run must be completed and validated")
            if run.config_snapshot is None:
                raise IneligibleSkillSourceError("source run has no reproducible config snapshot")
            snapshot = RunConfigSnapshot.model_validate(run.config_snapshot)
            if run.config_hash != snapshot.content_hash():
                raise IneligibleSkillSourceError("run config hash does not match its snapshot")
            if snapshot.skill_version_id is not None:
                raise IneligibleSkillSourceError("a skill-assisted run cannot become a source")
            calls = tuple(
                await unit.session.scalars(
                    select(ToolCallRecord).where(ToolCallRecord.run_id == run.id)
                )
            )
            if any(call.status is ToolCallStatus.DENIED for call in calls):
                raise IneligibleSkillSourceError("source contains a denied tool call")
            if any(ToolRisk(call.risk).value > self._max_risk.value for call in calls):
                raise IneligibleSkillSourceError("source exceeds the allowed risk")
            if any(call.tool_name not in self._allowed_tools for call in calls):
                raise IneligibleSkillSourceError(
                    "source used a tool outside the extraction allowlist"
                )
            unknown = await unit.session.scalar(
                select(ToolEffectRecord.id)
                .join(ToolCallRecord, ToolCallRecord.id == ToolEffectRecord.tool_call_id)
                .where(
                    ToolCallRecord.run_id == run.id,
                    ToolEffectRecord.status == ToolEffectStatus.UNKNOWN,
                )
                .limit(1)
            )
            pending = await unit.session.scalar(
                select(ToolApprovalRecord.id)
                .join(ToolCallRecord, ToolCallRecord.id == ToolApprovalRecord.tool_call_id)
                .where(
                    ToolCallRecord.run_id == run.id,
                    ToolApprovalRecord.status == ApprovalStatus.PENDING,
                )
                .limit(1)
            )
            if unknown is not None or pending is not None:
                raise IneligibleSkillSourceError(
                    "source contains unknown effects or pending approvals"
                )
            return eval_run


class ProvenanceService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        artifact_service: ArtifactService,
        sanitizer: TraceSanitizer,
        *,
        allowed_tools: frozenset[str] = frozenset(
            {"calculator", "file_read", "web_fetch", "web_search", "artifact_write"}
        ),
        max_risk: ToolRisk = ToolRisk.R1,
    ) -> None:
        self._session_factory = session_factory
        self._artifacts = artifact_service
        self._sanitizer = sanitizer
        self._eligibility = TraceEligibilityChecker(
            session_factory, allowed_tools=allowed_tools, max_risk=max_risk
        )

    async def freeze(self, eval_run_id: UUID) -> FrozenSkillSource:
        eval_run = await self._eligibility.check(eval_run_id)
        async with self._session_factory() as session:
            existing = await session.scalar(
                select(ArtifactRecord).where(
                    ArtifactRecord.run_id == eval_run.run_id,
                    ArtifactRecord.type == "application/vnd.evoagent.skill-source+json",
                )
            )
            if existing is not None and existing.attributes.get("eval_run_id") == str(eval_run_id):
                if existing.attributes.get("erased"):
                    raise IneligibleSkillSourceError("frozen source was erased")
                content = await self._artifacts.read_bounded(
                    existing.uri, max_bytes=8 * 1024 * 1024
                )
                actual_hash = "sha256:" + hashlib.sha256(content).hexdigest()
                if actual_hash != existing.content_hash:
                    raise IneligibleSkillSourceError("frozen source artifact was modified")
                payload = json.loads(content)
                if not isinstance(payload, dict):
                    raise IneligibleSkillSourceError("frozen source payload is invalid")
                sanitized = self._sanitizer.sanitize(payload)
                if sanitized.payload != payload:
                    raise IneligibleSkillSourceError("frozen source requires a new safe version")
                await self._eligibility.check(eval_run_id)
                await session.refresh(existing)
                if existing.attributes.get("erased") or existing.content_hash != actual_hash:
                    raise IneligibleSkillSourceError("frozen source changed during read")
                return FrozenSkillSource(
                    eval_run_id,
                    eval_run.run_id,
                    existing.id,
                    existing.content_hash,
                    payload,
                )

        bundle = await TraceBundleService(self._session_factory).build(
            eval_run.run_id,
            validation_results=tuple(
                {
                    "validator": item["validator"],
                    "version": item["version"],
                    "passed": item["passed"],
                }
                for item in eval_run.validation_results
            ),
        )
        for item in bundle.artifacts:
            raw = await self._artifacts.read(str(item["uri"]))
            actual_hash = "sha256:" + hashlib.sha256(raw).hexdigest()
            if actual_hash != item["content_hash"]:
                raise IneligibleSkillSourceError("source artifact hash does not match its content")
        sanitized: SanitizedTrace = self._sanitizer.sanitize(bundle.model_dump(mode="json"))
        content = json.dumps(
            sanitized.payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
        artifact = await self._artifacts.create_unique(
            run_id=eval_run.run_id,
            name=f"skill-source-{eval_run_id}.json",
            content=content,
            artifact_type="application/vnd.evoagent.skill-source+json",
            attributes={"eval_run_id": str(eval_run_id), "immutable": True},
        )
        return FrozenSkillSource(
            eval_run_id,
            eval_run.run_id,
            artifact.id,
            sanitized.source_trace_hash,
            sanitized.payload,
        )
