"""Bounded, consented multi-Run revision provenance; no raw history borrowing."""

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, update

from evoagent.db.models import (
    ArtifactRecord,
    LearningSourceRecord,
    RunFeedbackRecord,
    RunRecord,
    SessionRecord,
    SkillObservationRecord,
    SkillRecord,
    SkillVersionRecord,
    TaskRecord,
)
from evoagent.learning.schema import LearningError


class AggregationSourceRef(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    source_id: UUID
    run_id: UUID
    artifact_id: UUID
    content_hash: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
    source_revision: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
    revocation_epoch: int = Field(ge=0)
    feedback_id: UUID
    observation_id: UUID
    input_fingerprint: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
    independent_input_hash: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")


def aggregation_refs(request):
    frozen = request.policy_snapshot.get("revision_aggregation")
    if frozen is None:
        return ()
    try:
        if frozen["contract"] != "revision-aggregation:v1":
            raise ValueError("unsupported aggregation")
        refs = tuple(AggregationSourceRef.model_validate(r) for r in frozen["sources"])
        if (
            request.trigger != "discover"
            or request.target_skill_id is None
            or request.base_version_id is None
            or not 3 <= len(refs) <= 10
            or len({r.run_id for r in refs}) != len(refs)
            or len({r.input_fingerprint for r in refs}) != len(refs)
            or len({r.independent_input_hash for r in refs}) != len(refs)
            or sum(r.run_id == request.origin_run_id for r in refs) != 1
        ):
            raise ValueError("invalid aggregation")
        return refs
    except (ValueError, KeyError, TypeError):
        raise LearningError("revision_aggregation_identity_invalid") from None


async def verify_aggregation(session, request, source_service):
    refs = aggregation_refs(request)
    if not refs:
        return ()
    frozen = request.policy_snapshot["revision_aggregation"]
    skill = await session.get(
        SkillRecord, request.target_skill_id, with_for_update=True, populate_existing=True
    )
    base = await session.get(SkillVersionRecord, request.base_version_id)
    if (
        skill is None
        or base is None
        or base.skill_id != skill.id
        or skill.workspace_id != request.workspace_id
        or skill.project_id not in (None, request.project_id)
        or skill.lock_version != frozen.get("target_lock_version")
        or base.content_hash != frozen.get("base_content_hash")
    ):
        raise LearningError("revision_aggregation_target_changed")
    result = []
    for ref in refs:
        source = await session.get(LearningSourceRecord, ref.source_id)
        artifact = await session.get(ArtifactRecord, ref.artifact_id)
        observation = await session.get(SkillObservationRecord, ref.observation_id)
        latest_feedback = await session.scalar(
            select(RunFeedbackRecord)
            .where(RunFeedbackRecord.run_id == ref.run_id)
            .order_by(RunFeedbackRecord.revision.desc())
            .limit(1)
        )
        latest_observation_revision = await session.scalar(
            select(SkillObservationRecord.feedback_revision)
            .where(
                SkillObservationRecord.run_id == ref.run_id,
                SkillObservationRecord.version_id == base.id,
            )
            .order_by(SkillObservationRecord.feedback_revision.desc())
            .limit(1)
        )
        run = await session.get(RunRecord, ref.run_id)
        task = await session.get(TaskRecord, run.task_id) if run else None
        chat = await session.get(SessionRecord, task.session_id) if task else None
        if (
            source is None
            or source.status != "valid"
            or source.run_id != ref.run_id
            or source.source_role != "personal"
            or source.artifact_id != ref.artifact_id
            or source.feedback_id != ref.feedback_id
            or source.content_hash != ref.content_hash
            or source.source_revision != ref.source_revision
            or source.revocation_epoch != ref.revocation_epoch
            or str(base.id) not in source.parent_skill_versions
            or artifact is None
            or artifact.attributes.get("erased")
            or artifact.redaction_status == "quarantined"
            or artifact.content_hash != ref.content_hash
            or latest_feedback is None
            or latest_feedback.id != ref.feedback_id
            or not latest_feedback.learn_from_feedback
            or latest_feedback.intent != "method"
            or latest_feedback.verdict not in {"needs_fix", "incorrect"}
            or observation is None
            or observation.run_id != ref.run_id
            or observation.version_id != base.id
            or observation.feedback_revision != latest_feedback.revision
            or observation.feedback_revision != latest_observation_revision
            or observation.input_fingerprint != ref.input_fingerprint
            or observation.outcome != "verified_failure"
            or observation.attribution != "skill_related"
            or observation.evidence.get("verification_origin") not in {"user", "machine"}
            or observation.evidence.get("criterion_id") != frozen.get("criterion_id")
            or sorted(observation.evidence.get("associated_steps", ()))
            != frozen.get("associated_steps")
            or not observation.evidence.get("evidence_refs")
            or chat is None
            or chat.workspace_id != request.workspace_id
            or task.project_id != request.project_id
        ):
            raise LearningError("revision_aggregation_source_changed")
        await source_service.check_in_session(session, ref.run_id, ref.feedback_id, "feedback")
        from evoagent.skills.provenance import formal_input_fingerprint

        evidence = await source_service.build_evidence_in_session(
            session, ref.run_id, ref.feedback_id
        )
        if formal_input_fingerprint(evidence) != ref.independent_input_hash:
            raise LearningError("revision_aggregation_input_changed")
        if ref.run_id == request.origin_run_id and (
            ref.source_revision != request.frozen_inputs.get("source_revision")
            or str(ref.feedback_id) != request.frozen_inputs.get("feedback_id")
        ):
            raise LearningError("revision_aggregation_primary_changed")
        result.append(source)
    return tuple(result)


async def lock_aggregation_runs(session, request):
    # All Run locks precede Workspace, Request and Job locks. Never acquire a
    # secondary Run after holding the Workspace mutex.
    for run_id in sorted({ref.run_id for ref in aggregation_refs(request)}, key=str):
        await session.execute(
            update(RunRecord)
            .where(RunRecord.id == run_id)
            .values(next_feedback_revision=RunRecord.next_feedback_revision)
        )
