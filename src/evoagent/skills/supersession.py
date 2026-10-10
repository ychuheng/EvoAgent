"""Human-confirmed supersession only after a healthy validated merge trial."""

from datetime import UTC, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, update

from evoagent.db.models import (
    LearningRequestRecord,
    SkillRecord,
    SkillTrialRecord,
    SkillVersionRecord,
    WorkspaceRecord,
)
from evoagent.db.repositories.base import ConcurrentUpdateError
from evoagent.db.repositories.skills import SkillRepository
from evoagent.learning.schema import LearningError
from evoagent.privacy.redaction import detect_sensitive
from evoagent.skills.access import SkillAccessError, SkillAccessPolicy
from evoagent.skills.lifecycle import SkillStatus
from evoagent.skills.trials import SkillTrialService, TrialScope
from evoagent.skills.usage import SkillUsageService


class SupersessionSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid")
    replacement_id: UUID
    replacement_version_id: UUID
    replacement_trial_id: UUID
    expected_lock_version: int = Field(ge=0)
    expected_replacement_lock_version: int = Field(ge=0)
    expected_trial_lock_version: int = Field(ge=0)
    reason: str = Field(min_length=1, max_length=1000)


class SkillSupersessionService:
    def __init__(self, factory):
        self.factory = factory

    async def deprecate(self, skill_id, payload: SupersessionSubmission):
        if not payload.reason.strip() or detect_sensitive(payload.reason):
            raise LearningError("supersession_safe_reason_required")
        async with self.factory() as session:
            old = await session.get(SkillRecord, skill_id)
            replacement = await session.get(SkillRecord, payload.replacement_id)
            if old is None or replacement is None:
                raise LearningError("supersession_skill_not_found")
            if (
                old.id == replacement.id
                or old.workspace_id != replacement.workspace_id
                or old.project_id != replacement.project_id
            ):
                raise LearningError("supersession_scope_mismatch")
            expected_scope = (old.workspace_id, old.project_id)
            await session.execute(
                update(WorkspaceRecord)
                .where(WorkspaceRecord.id == old.workspace_id)
                .values(name=WorkspaceRecord.name)
            )
            for identity in sorted((old.id, replacement.id), key=str):
                await session.scalar(
                    select(SkillRecord)
                    .where(SkillRecord.id == identity)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
            if (old.workspace_id, old.project_id) != expected_scope or (
                replacement.workspace_id,
                replacement.project_id,
            ) != expected_scope:
                raise LearningError("supersession_scope_changed")
            if (
                old.lock_version != payload.expected_lock_version
                or replacement.lock_version != payload.expected_replacement_lock_version
            ):
                raise ConcurrentUpdateError("supersession_version_conflict")
            if (
                old.status is not SkillStatus.ENABLED
                or replacement.status is not SkillStatus.ENABLED
            ):
                raise LearningError("supersession_skill_not_enabled")
            version = await session.get(SkillVersionRecord, payload.replacement_version_id)
            if (
                version is None
                or version.skill_id != replacement.id
                or not version.extraction_key.startswith("merge:")
            ):
                raise LearningError("supersession_merge_candidate_required")
            try:
                await SkillAccessPolicy().check(
                    session, version.id, workspace_id=old.workspace_id, project_id=old.project_id
                )
            except SkillAccessError as error:
                raise LearningError("supersession_source_invalid") from error
            proposal = await session.scalar(
                select(LearningRequestRecord).where(
                    LearningRequestRecord.candidate_version_id == version.id,
                    LearningRequestRecord.trigger == "merge",
                    LearningRequestRecord.request_kind == "propose",
                )
            )
            if proposal is None or not any(
                p["skill_id"] == str(old.id) for p in proposal.policy_snapshot["lineage"]
            ):
                raise LearningError("supersession_parent_not_in_merge")
            trial = await session.scalar(
                select(SkillTrialRecord)
                .where(SkillTrialRecord.id == payload.replacement_trial_id)
                .with_for_update()
            )
            if (
                trial is None
                or trial.skill_id != replacement.id
                or trial.version_id != version.id
                or trial.status != "active"
                or trial.workspace_id != old.workspace_id
                or trial.project_id != old.project_id
                or trial.scope_key != TrialScope(old.workspace_id, old.project_id).key
            ):
                raise LearningError("supersession_active_same_scope_trial_required")
            if trial.lock_version != payload.expected_trial_lock_version:
                raise ConcurrentUpdateError("supersession_trial_version_conflict")
            trials = SkillTrialService(self.factory)
            readiness = await trials._assess(session, version.id, trial.validation_request_id)
            if not readiness.ready or readiness.report_hash != trial.report_hash:
                raise LearningError("supersession_verified_validation_required")
            health = await SkillUsageService(self.factory).health_snapshot(session, trial)
            if health.status != "healthy":
                raise LearningError("supersession_healthy_replacement_required")
            old_bindings = list(
                await session.scalars(
                    select(SkillTrialRecord)
                    .where(SkillTrialRecord.skill_id == old.id, SkillTrialRecord.status == "active")
                    .with_for_update()
                    .limit(201)
                )
            )
            if len(old_bindings) > 200:
                raise LearningError("supersession_binding_budget_exceeded")
            old_versions = list(
                await session.scalars(
                    select(SkillVersionRecord.id)
                    .where(SkillVersionRecord.skill_id == old.id)
                    .limit(201)
                )
            )
            if len(old_versions) > 200:
                raise LearningError("supersession_version_budget_exceeded")
            now = datetime.now(UTC)
            for binding in old_bindings:
                binding.status = "replaced"
                binding.lock_version += 1
            old.status = SkillStatus.DEPRECATED
            old.superseded_by_skill_id = replacement.id
            old.lock_version += 1
            # A legacy retrieval reference may treat deprecation as revocation.
            # Prove the replacement is still usable before committing any old
            # status or binding changes; never leave a broken replacement.
            try:
                await SkillAccessPolicy().check(
                    session, version.id, workspace_id=old.workspace_id, project_id=old.project_id
                )
            except SkillAccessError as error:
                raise LearningError("supersession_would_revoke_replacement") from error
            from evoagent.db.models import SkillEventRecord

            session.add(
                SkillEventRecord(
                    skill_id=old.id,
                    sequence=await SkillRepository(session).allocate_event_sequence(old.id),
                    event_type="skill.superseded",
                    created_at=now,
                    payload={
                        "actor": "local-user",
                        "reason": payload.reason.strip(),
                        "replacement_skill_id": str(replacement.id),
                        "replacement_version_id": str(version.id),
                        "replacement_trial_id": str(trial.id),
                        "validation_report_hash": trial.report_hash,
                        "replaced_trial_ids": [str(binding.id) for binding in old_bindings],
                    },
                )
            )
            from evoagent.retrieval.indexing import enqueue_source

            for old_version_id in old_versions:
                await enqueue_source(session, f"skill:{old_version_id}")
            await session.commit()
            return {
                "skill_id": str(old.id),
                "status": old.status.value,
                "lock_version": old.lock_version,
                "superseded_by_skill_id": str(replacement.id),
                "replaced_trial_count": len(old_bindings),
            }
