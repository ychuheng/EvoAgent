"""Common formal eligibility reader; trial selection remains closed."""

from dataclasses import dataclass

from sqlalchemy import select

from evoagent.db.models import (
    RunSkillSelectionRecord,
    SessionRecord,
    SkillRecord,
    SkillVersionRecord,
)
from evoagent.memory.schema import MemoryError
from evoagent.privacy.redaction import redact_value
from evoagent.skills.applicability import SkillApplicabilityEvaluator
from evoagent.skills.canonical import content_hash
from evoagent.skills.schema import SkillDefinition


@dataclass(frozen=True)
class EligibleFormalSkill:
    version: SkillVersionRecord
    definition: SkillDefinition


class FormalSkillReader:
    async def check_run_bindings(self, session, *, task, run):
        # Pinned experiments have their own fenced source/target authorization.
        # This reader does not grant access to DRAFTs or trial bindings.
        if run.run_mode != "retrieval":
            return
        rows = list(
            await session.execute(
                select(SkillRecord, SkillVersionRecord)
                .join(SkillVersionRecord, SkillVersionRecord.skill_id == SkillRecord.id)
                .join(
                    RunSkillSelectionRecord,
                    RunSkillSelectionRecord.skill_version_id == SkillVersionRecord.id,
                )
                .where(RunSkillSelectionRecord.run_id == run.id)
            )
        )
        if not rows:
            return
        workspace_id = await session.scalar(
            select(SessionRecord.workspace_id).where(SessionRecord.id == task.session_id)
        )
        expected = {
            item["version_id"]: item["content_hash"]
            for item in (run.config_snapshot or {}).get("selected_skills", []) or []
        }
        for skill, version in rows:
            try:
                self.check_frozen_scope(
                    skill, workspace_id=workspace_id, project_id=task.project_id
                )
            except MemoryError:
                raise MemoryError("skill_source_revoked") from None
            if (
                version.lifecycle_status.value == "rejected"
                or content_hash(version.definition) != version.content_hash
                or str(version.id) in expected
                and expected[str(version.id)] != version.content_hash
                or redact_value(version.definition) != version.definition
            ):
                raise MemoryError("skill_source_revoked")

    @staticmethod
    def scope_allows(skill, *, workspace_id, project_id=None):
        # There is no implicit global sharing grant. DEFAULT_WORKSPACE_ID is a
        # real scope, not a wildcard. Formal sharing needs its own proven policy.
        return skill.workspace_id == workspace_id and skill.project_id in (None, project_id)

    @classmethod
    def check_frozen_scope(cls, skill, *, workspace_id, project_id=None):
        # A superseded active pointer must not silently change a frozen method.
        # Scope/explicit disable are still revocation boundaries on restore.
        if (
            skill is None
            or skill.status.value != "enabled"
            or not cls.scope_allows(skill, workspace_id=workspace_id, project_id=project_id)
        ):
            raise MemoryError("context_source_revoked")

    async def read(
        self,
        session,
        version_id,
        *,
        workspace_id=None,
        project_id=None,
        registry=None,
        max_risk="R1",
        facts=None,
        lock=False,
    ):
        version = await session.get(SkillVersionRecord, version_id)
        skill = await session.get(SkillRecord, version.skill_id) if version else None
        if skill is None or (
            workspace_id is not None
            and not self.scope_allows(skill, workspace_id=workspace_id, project_id=project_id)
        ):
            return None
        if lock:
            skill = await session.scalar(
                select(SkillRecord)
                .where(SkillRecord.id == skill.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            await session.refresh(version)
        if (
            skill.status.value != "enabled"
            or skill.active_version_id != version_id
            or version.lifecycle_status.value != "active"
            or workspace_id is not None
            and not self.scope_allows(skill, workspace_id=workspace_id, project_id=project_id)
        ):
            return None
        if content_hash(version.definition) != version.content_hash:
            raise MemoryError("retrieval_source_hash_mismatch")
        definition = SkillDefinition.model_validate(version.definition)
        if redact_value(version.definition) != version.definition:
            return None
        allowed = set(definition.preconditions.allowed_tools)
        if (
            "shell" in allowed
            or definition.preconditions.max_effective_risk.value > max_risk
            or registry is not None
            and not allowed <= set(registry.names)
            or facts is not None
            and SkillApplicabilityEvaluator().assess(definition, facts).status != "applicable"
        ):
            return None
        return EligibleFormalSkill(version, definition)
