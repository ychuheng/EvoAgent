"""Skill 查询、版本差异、人工决定、原子发布与回滚服务。"""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from evoagent.db.models import (
    EvalExperimentRecord,
    PromotionDecisionRecord,
    SkillEventRecord,
    SkillRecord,
    SkillSourceRecord,
    SkillVersionRecord,
)
from evoagent.db.repositories.base import RecordNotFoundError
from evoagent.db.unit_of_work import UnitOfWork
from evoagent.evals.gates import GateReport
from evoagent.evals.lifecycle import PromotionAction
from evoagent.skills.canonical import content_hash
from evoagent.skills.lifecycle import (
    SkillStatus,
    SkillVersionStatus,
    ensure_skill_transition,
    ensure_skill_version_transition,
)
from evoagent.skills.schema import SkillDefinition
from evoagent.skills.validation import SkillDefinitionValidator


class SkillServiceError(RuntimeError):
    code = "skill_service_error"


class SkillVersionConflictError(SkillServiceError):
    code = "version_conflict"


class InvalidSkillTransitionError(SkillServiceError):
    code = "invalid_skill_transition"


class GateNotPassedError(SkillServiceError):
    code = "gate_not_passed"


class SkillCompatibilityError(SkillServiceError):
    code = "skill_incompatible"


@dataclass(frozen=True, slots=True)
class PromotionResult:
    skill_id: UUID
    active_version_id: UUID | None
    status: SkillStatus
    lock_version: int


class SkillService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        validator: SkillDefinitionValidator,
    ) -> None:
        self._session_factory = session_factory
        self._validator = validator

    async def list_skills(self) -> tuple[dict[str, Any], ...]:
        async with self._session_factory() as session:
            skills = tuple(await session.scalars(select(SkillRecord).order_by(SkillRecord.slug)))
            return tuple(self._skill_summary(item) for item in skills)

    async def get_skill(self, skill_id: UUID) -> dict[str, Any]:
        async with UnitOfWork(self._session_factory) as unit:
            skill = await unit.skills.get(skill_id)
            versions = tuple(
                await unit.session.scalars(
                    select(SkillVersionRecord)
                    .where(SkillVersionRecord.skill_id == skill.id)
                    .order_by(SkillVersionRecord.version.desc())
                )
            )
            return {
                **self._skill_summary(skill),
                "versions": [self._version_summary(item) for item in versions],
            }

    async def get_version(self, version_id: UUID) -> dict[str, Any]:
        async with UnitOfWork(self._session_factory) as unit:
            version = await unit.skill_versions.get(version_id)
            sources = tuple(
                await unit.session.scalars(
                    select(SkillSourceRecord)
                    .where(SkillSourceRecord.skill_version_id == version.id)
                    .order_by(SkillSourceRecord.id)
                )
            )
            experiment = await unit.session.scalar(
                select(EvalExperimentRecord)
                .where(EvalExperimentRecord.skill_version_id == version.id)
                .order_by(EvalExperimentRecord.created_at.desc())
                .limit(1)
            )
            return {
                **self._version_summary(version),
                "definition": version.definition,
                "sources": [
                    {
                        "source_run_id": str(item.source_run_id),
                        "source_eval_run_id": str(item.source_eval_run_id),
                        "trace_artifact_id": str(item.trace_artifact_id),
                        "source_trace_hash": item.source_trace_hash,
                    }
                    for item in sources
                ],
                "gate_report": experiment.gate_report if experiment is not None else None,
            }

    async def create_version(
        self,
        *,
        skill_id: UUID,
        definition: SkillDefinition,
        parent_version_id: UUID | None,
    ) -> SkillVersionRecord:
        self._validator.validate(definition)
        definition_json = definition.model_dump(mode="json")
        digest = content_hash(definition_json)
        extraction_key = content_hash(
            {
                "kind": "manual_revision",
                "skill_id": str(skill_id),
                "parent_version_id": str(parent_version_id) if parent_version_id else None,
                "definition_hash": digest,
            }
        )
        async with UnitOfWork(self._session_factory) as unit:
            skill = await unit.skills.get(skill_id, for_update=True)
            existing = await unit.skill_versions.find_by_extraction_key(extraction_key)
            if existing is not None:
                return existing
            if definition.name != skill.slug:
                raise ValueError("revised definition name must match the skill slug")
            if parent_version_id is not None:
                parent = await unit.skill_versions.get(parent_version_id)
                if parent.skill_id != skill.id:
                    raise ValueError("parent version belongs to another skill")
            version = SkillVersionRecord(
                skill_id=skill.id,
                parent_version_id=parent_version_id,
                version=await unit.skills.next_version(skill.id),
                schema_version=definition.schema_version,
                definition=definition_json,
                content_hash=digest,
                extraction_key=extraction_key,
                lifecycle_status=SkillVersionStatus.DRAFT,
            )
            unit.skill_versions.add(version)
            await unit.session.flush()
            await self._event(
                unit,
                skill.id,
                "skill.version_drafted",
                {"skill_version_id": str(version.id), "source": "manual_revision"},
            )
            await unit.commit()
            return version

    async def diff_versions(self, version_id: UUID, against: UUID) -> tuple[dict[str, Any], ...]:
        async with UnitOfWork(self._session_factory) as unit:
            current = await unit.skill_versions.get(version_id)
            previous = await unit.skill_versions.get(against)
            if current.skill_id != previous.skill_id:
                raise ValueError("versions from different skills cannot be compared")
        changes: list[dict[str, Any]] = []
        self._diff("$", previous.definition, current.definition, changes)
        return tuple(changes)

    async def approve(
        self,
        *,
        version_id: UUID,
        expected_lock_version: int,
        reviewer: str,
        reason: str,
    ) -> PromotionResult:
        return await self._publish(
            version_id=version_id,
            expected_lock_version=expected_lock_version,
            reviewer=reviewer,
            reason=reason,
            action=PromotionAction.APPROVE,
        )

    async def reject(
        self,
        *,
        version_id: UUID,
        expected_lock_version: int,
        reviewer: str,
        reason: str,
    ) -> PromotionResult:
        async with UnitOfWork(self._session_factory) as unit:
            version = await unit.skill_versions.get(version_id)
            skill = await self._locked_skill(unit, version.skill_id, expected_lock_version)
            try:
                ensure_skill_version_transition(
                    version.lifecycle_status, SkillVersionStatus.REJECTED
                )
            except ValueError as error:
                raise InvalidSkillTransitionError(str(error)) from error
            version.lifecycle_status = SkillVersionStatus.REJECTED
            skill.lock_version += 1
            self._decision(unit, version.id, PromotionAction.REJECT, reviewer, reason)
            await self._event(
                unit,
                skill.id,
                "skill.version_rejected",
                {"skill_version_id": str(version.id), "reviewer": reviewer},
            )
            await unit.commit()
            return self._result(skill)

    async def set_status(
        self,
        *,
        skill_id: UUID,
        target: SkillStatus,
        expected_lock_version: int,
        reviewer: str,
        reason: str,
    ) -> PromotionResult:
        action_by_target = {
            SkillStatus.ENABLED: PromotionAction.ENABLE,
            SkillStatus.DISABLED: PromotionAction.DISABLE,
            SkillStatus.DEPRECATED: PromotionAction.DEPRECATE,
        }
        async with UnitOfWork(self._session_factory) as unit:
            skill = await self._locked_skill(unit, skill_id, expected_lock_version)
            try:
                ensure_skill_transition(skill.status, target)
            except ValueError as error:
                raise InvalidSkillTransitionError(str(error)) from error
            skill.status = target
            skill.lock_version += 1
            if skill.active_version_id is not None:
                self._decision(
                    unit,
                    skill.active_version_id,
                    action_by_target[target],
                    reviewer,
                    reason,
                )
            await self._event(
                unit,
                skill.id,
                f"skill.{target.value}",
                {"reviewer": reviewer, "reason": reason},
            )
            await unit.commit()
            return self._result(skill)

    async def rollback(
        self,
        *,
        skill_id: UUID,
        target_version_id: UUID,
        expected_lock_version: int,
        reviewer: str,
        reason: str,
    ) -> PromotionResult:
        async with UnitOfWork(self._session_factory) as unit:
            skill = await self._locked_skill(unit, skill_id, expected_lock_version)
            target = await unit.skill_versions.get(target_version_id)
            if target.skill_id != skill.id:
                raise InvalidSkillTransitionError("rollback target belongs to another skill")
            if target.lifecycle_status is not SkillVersionStatus.RETIRED:
                raise InvalidSkillTransitionError("rollback target must be retired")
            if target.gate_report_hash is None:
                raise GateNotPassedError("rollback target has no accepted gate report")
            try:
                self._validator.validate(SkillDefinition.model_validate(target.definition))
            except ValueError as error:
                raise SkillCompatibilityError(str(error)) from error
            if skill.active_version_id is None:
                raise InvalidSkillTransitionError("skill has no active version to roll back")
            current = await unit.skill_versions.get(skill.active_version_id)
            if current.lifecycle_status is not SkillVersionStatus.ACTIVE:
                raise InvalidSkillTransitionError("active version pointer is inconsistent")
            ensure_skill_version_transition(current.lifecycle_status, SkillVersionStatus.RETIRED)
            ensure_skill_version_transition(target.lifecycle_status, SkillVersionStatus.ACTIVE)
            current.lifecycle_status = SkillVersionStatus.RETIRED
            target.lifecycle_status = SkillVersionStatus.ACTIVE
            skill.active_version_id = target.id
            skill.lock_version += 1
            self._decision(unit, target.id, PromotionAction.ROLLBACK, reviewer, reason)
            await self._event(
                unit,
                skill.id,
                "skill.version_rolled_back",
                {
                    "from_version_id": str(current.id),
                    "to_version_id": str(target.id),
                    "reviewer": reviewer,
                },
            )
            await unit.commit()
            return self._result(skill)

    async def _publish(
        self,
        *,
        version_id: UUID,
        expected_lock_version: int,
        reviewer: str,
        reason: str,
        action: PromotionAction,
    ) -> PromotionResult:
        async with UnitOfWork(self._session_factory) as unit:
            version = await unit.skill_versions.get(version_id)
            skill = await self._locked_skill(unit, version.skill_id, expected_lock_version)
            if version.lifecycle_status is not SkillVersionStatus.REVIEW_REQUIRED:
                raise InvalidSkillTransitionError("only review_required versions can be approved")
            await self._require_gate(unit, version)
            if skill.active_version_id is not None:
                current = await unit.skill_versions.get(skill.active_version_id)
                if current.id == version.id:
                    return self._result(skill)
                if current.lifecycle_status is not SkillVersionStatus.ACTIVE:
                    raise InvalidSkillTransitionError("active version pointer is inconsistent")
                ensure_skill_version_transition(
                    current.lifecycle_status, SkillVersionStatus.RETIRED
                )
                current.lifecycle_status = SkillVersionStatus.RETIRED
            ensure_skill_version_transition(version.lifecycle_status, SkillVersionStatus.ACTIVE)
            version.lifecycle_status = SkillVersionStatus.ACTIVE
            skill.active_version_id = version.id
            skill.lock_version += 1
            self._decision(
                unit,
                version.id,
                action,
                reviewer,
                reason,
                gate_report_hash=version.gate_report_hash,
            )
            await self._event(
                unit,
                skill.id,
                "skill.version_activated",
                {"skill_version_id": str(version.id), "reviewer": reviewer},
            )
            await unit.commit()
            return self._result(skill)

    async def _locked_skill(self, unit, skill_id: UUID, expected: int) -> SkillRecord:
        skill = await unit.session.scalar(
            select(SkillRecord).where(SkillRecord.id == skill_id).with_for_update()
        )
        if skill is None:
            raise RecordNotFoundError(f"skill does not exist: {skill_id}")
        if skill.lock_version != expected:
            raise SkillVersionConflictError(
                f"expected lock_version {expected}, actual {skill.lock_version}"
            )
        # PostgreSQL 行锁提供串行化；额外 CAS 让不支持 FOR UPDATE 的 SQLite 测试也能发现冲突。
        result = await unit.session.execute(
            update(SkillRecord)
            .where(SkillRecord.id == skill.id, SkillRecord.lock_version == expected)
            .values(lock_version=expected)
        )
        if result.rowcount != 1:
            raise SkillVersionConflictError("skill was concurrently modified")
        return skill

    @staticmethod
    async def _require_gate(unit, version: SkillVersionRecord) -> None:
        if version.gate_report_hash is None:
            raise GateNotPassedError("skill version has no gate report")
        experiment = await unit.session.scalar(
            select(EvalExperimentRecord)
            .where(
                EvalExperimentRecord.skill_version_id == version.id,
                EvalExperimentRecord.gate_report_hash == version.gate_report_hash,
            )
            .order_by(EvalExperimentRecord.created_at.desc())
            .limit(1)
        )
        if experiment is None or experiment.gate_report is None:
            raise GateNotPassedError("accepted gate report does not exist")
        report = GateReport.model_validate(experiment.gate_report)
        if (
            not report.passed
            or report.skill_version_id != version.id
            or report.experiment_id != experiment.id
            or report.report_hash() != version.gate_report_hash
        ):
            raise GateNotPassedError("quality gate did not pass or its hash changed")

    @staticmethod
    def _decision(
        unit,
        version_id: UUID,
        action: PromotionAction,
        reviewer: str,
        reason: str,
        *,
        gate_report_hash: str | None = None,
    ) -> None:
        normalized_reviewer = reviewer.strip()
        normalized_reason = reason.strip()
        if not normalized_reviewer or not normalized_reason:
            raise ValueError("reviewer and reason cannot be blank")
        unit.session.add(
            PromotionDecisionRecord(
                skill_version_id=version_id,
                action=action,
                reviewer=normalized_reviewer,
                reason=normalized_reason,
                gate_report_hash=gate_report_hash,
            )
        )

    @staticmethod
    async def _event(unit, skill_id: UUID, event_type: str, payload: dict[str, Any]) -> None:
        sequence = (
            int(
                await unit.session.scalar(
                    select(func.coalesce(func.max(SkillEventRecord.sequence), 0)).where(
                        SkillEventRecord.skill_id == skill_id
                    )
                )
                or 0
            )
            + 1
        )
        unit.session.add(
            SkillEventRecord(
                skill_id=skill_id,
                sequence=sequence,
                event_type=event_type,
                payload=payload,
                created_at=datetime.now(UTC),
            )
        )

    @staticmethod
    def _skill_summary(skill: SkillRecord) -> dict[str, Any]:
        return {
            "id": str(skill.id),
            "name": skill.name,
            "slug": skill.slug,
            "description": skill.description,
            "status": skill.status.value,
            "active_version_id": (
                str(skill.active_version_id) if skill.active_version_id is not None else None
            ),
            "lock_version": skill.lock_version,
            "created_at": skill.created_at,
            "updated_at": skill.updated_at,
        }

    @staticmethod
    def _version_summary(version: SkillVersionRecord) -> dict[str, Any]:
        return {
            "id": str(version.id),
            "skill_id": str(version.skill_id),
            "parent_version_id": (
                str(version.parent_version_id) if version.parent_version_id else None
            ),
            "version": version.version,
            "schema_version": version.schema_version,
            "content_hash": version.content_hash,
            "lifecycle_status": version.lifecycle_status.value,
            "evaluation_report_hash": version.evaluation_report_hash,
            "gate_report_hash": version.gate_report_hash,
            "created_at": version.created_at,
        }

    @staticmethod
    def _result(skill: SkillRecord) -> PromotionResult:
        return PromotionResult(skill.id, skill.active_version_id, skill.status, skill.lock_version)

    @classmethod
    def _diff(
        cls,
        path: str,
        before: Any,
        after: Any,
        changes: list[dict[str, Any]],
    ) -> None:
        if type(before) is not type(after):
            changes.append({"path": path, "kind": "changed", "before": before, "after": after})
            return
        if isinstance(before, dict):
            for key in sorted(before.keys() | after.keys()):
                child = f"{path}.{key}"
                if key not in before:
                    changes.append(
                        {"path": child, "kind": "added", "before": None, "after": after[key]}
                    )
                elif key not in after:
                    changes.append(
                        {"path": child, "kind": "removed", "before": before[key], "after": None}
                    )
                else:
                    cls._diff(child, before[key], after[key], changes)
            return
        if isinstance(before, list):
            if before != after:
                changes.append({"path": path, "kind": "changed", "before": before, "after": after})
            return
        if before != after:
            changes.append({"path": path, "kind": "changed", "before": before, "after": after})
