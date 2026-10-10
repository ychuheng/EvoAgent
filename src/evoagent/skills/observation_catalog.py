"""Bounded metadata for explicit user judgments; never expose artifact bodies."""

from sqlalchemy import or_, select

from evoagent.db.models import (
    ArtifactRecord,
    RunRecord,
    RunSkillSelectionRecord,
    SkillVersionRecord,
)
from evoagent.learning.schema import LearningError
from evoagent.privacy.artifact_access import EVIDENCE_TEXT_ARTIFACT_TYPES
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
                    ArtifactRecord.attributes["erased"].as_boolean().is_not(True),
                    ArtifactRecord.redaction_status == "verified",
                    ArtifactRecord.redaction_policy_version == POLICY_VERSION,
                    ArtifactRecord.redaction_checked_hash == ArtifactRecord.content_hash,
                )
                .order_by(ArtifactRecord.created_at, ArtifactRecord.id)
                .limit(201)
            )
        )
        pending = list(
            await session.scalars(
                select(ArtifactRecord)
                .where(
                    ArtifactRecord.run_id == run_id,
                    ArtifactRecord.attributes["erased"].as_boolean().is_not(True),
                    ArtifactRecord.type.in_(EVIDENCE_TEXT_ARTIFACT_TYPES),
                    ArtifactRecord.redaction_status.in_(("unchecked", "verified")),
                    or_(
                        ArtifactRecord.redaction_status == "unchecked",
                        ArtifactRecord.redaction_policy_version != POLICY_VERSION,
                        ArtifactRecord.redaction_policy_version.is_(None),
                        ArtifactRecord.redaction_checked_hash != ArtifactRecord.content_hash,
                        ArtifactRecord.redaction_checked_hash.is_(None),
                    ),
                )
                .order_by(ArtifactRecord.created_at, ArtifactRecord.id)
                .limit(201)
            )
        )
        current_ids = {row.id for row in artifacts if not row.attributes.get("erased")}
        pending = [
            row
            for row in pending
            if row.id not in current_ids
            and not row.attributes.get("erased")
            and row.redaction_status != "not_applicable"
        ]
        return {
            "pending_artifacts_truncated": len(pending) > 200,
            "pending_artifacts": [
                {"artifact_id": row.id, "content_hash": row.content_hash, "type": row.type}
                for row in pending[:200]
            ],
            "run_id": run_id,
            "versions": versions,
            "artifacts_truncated": len(artifacts) > 200,
            "artifacts": [
                {"artifact_id": row.id, "content_hash": row.content_hash, "type": row.type}
                for row in artifacts[:200]
                if not row.attributes.get("erased")
            ],
        }


async def verify_observation_evidence(factory, store, settings, run_id, references):
    from evoagent.privacy.artifact_access import ArtifactInjectionGuard
    from evoagent.tools.base import ToolError

    # Validate personal terminal identity before any filesystem reads. The
    # existing guard rechecks run ownership/revocation/hash around every scan.
    catalog = await observation_catalog(factory, run_id)
    if not catalog["versions"]:
        raise LearningError("observation_selection_required")
    guard = ArtifactInjectionGuard(session_factory=factory, artifact_store=store, settings=settings)
    for reference in references:
        try:
            await guard.verify_feedback_evidence(
                artifact_id=reference.artifact_id,
                run_id=run_id,
                expected_hash=reference.content_hash,
            )
        except (ToolError, OSError, ValueError) as error:
            raise LearningError("observation_evidence_check_denied") from error
    return await observation_catalog(factory, run_id)
