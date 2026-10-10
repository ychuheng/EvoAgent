"""Bounded metadata for explicit user judgments; never expose artifact bodies."""

from sqlalchemy import select

from evoagent.db.models import (
    ArtifactRecord,
    RunRecord,
    RunSkillSelectionRecord,
    SkillVersionRecord,
)
from evoagent.learning.schema import LearningError
from evoagent.privacy.redaction import POLICY_VERSION
from evoagent.skills.observation_evidence import OBSERVABLE_TERMINAL_STATES


async def observation_catalog(factory, run_id):
    async with factory() as session:
        run = await session.get(RunRecord, run_id)
        if run is None:
            raise LearningError("run_not_found")
        if run.data_role != "personal" or run.status not in OBSERVABLE_TERMINAL_STATES:
            raise LearningError("observation_terminal_personal_run_required")
        bindings = list(
            await session.scalars(
                select(RunSkillSelectionRecord)
                .where(
                    RunSkillSelectionRecord.run_id == run_id,
                    RunSkillSelectionRecord.selection_policy_version == "skill-selector-v1",
                    RunSkillSelectionRecord.origin.in_(("formal", "trial")),
                )
                .order_by(RunSkillSelectionRecord.rank)
                .limit(2)
            )
        )
        if len(bindings) > 1:
            raise LearningError("observation_selection_invalid")
        versions = []
        for binding in bindings:
            version = await session.get(SkillVersionRecord, binding.skill_version_id)
            if version is None:
                raise LearningError("observation_selection_invalid")
            versions.append(
                {
                    "version_id": version.id,
                    "origin": binding.origin,
                    "steps": [step["id"] for step in version.definition.get("steps", ())],
                }
            )
        artifacts = list(
            await session.scalars(
                select(ArtifactRecord)
                .where(
                    ArtifactRecord.run_id == run_id,
                    ArtifactRecord.redaction_status == "verified",
                    ArtifactRecord.redaction_policy_version == POLICY_VERSION,
                    ArtifactRecord.redaction_checked_hash == ArtifactRecord.content_hash,
                )
                .order_by(ArtifactRecord.created_at, ArtifactRecord.id)
                .limit(201)
            )
        )
        return {
            "run_id": run_id,
            "versions": versions,
            "artifacts_truncated": len(artifacts) > 200,
            "artifacts": [
                {"artifact_id": row.id, "content_hash": row.content_hash, "type": row.type}
                for row in artifacts[:200]
                if not row.attributes.get("erased")
            ],
        }
