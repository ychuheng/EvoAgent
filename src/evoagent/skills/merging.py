"""Explicit local-user merge proposals; no model calls or automatic adoption."""

import asyncio
from datetime import UTC, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, update

from evoagent.db.models import (
    LearningSourceRecord,
    RunFeedbackRecord,
    RunRecord,
    SkillEventRecord,
    SkillRecord,
    SkillSourceRecord,
    SkillVersionRecord,
    WorkspaceRecord,
)
from evoagent.db.repositories.base import ConcurrentUpdateError
from evoagent.db.repositories.events import RunEventRepository
from evoagent.learning.repository import LearningRepository
from evoagent.learning.schema import LearningError
from evoagent.learning.service import LearningService, request_view
from evoagent.learning.sources import PersonalSourceService
from evoagent.memory.schema import MemoryError
from evoagent.privacy.redaction import detect_sensitive
from evoagent.skills.access import SkillAccessError, SkillAccessPolicy
from evoagent.skills.canonical import canonical_json, content_hash
from evoagent.skills.extraction import require_s6_annotations
from evoagent.skills.lifecycle import SkillVersionStatus
from evoagent.skills.schema import SkillDefinition
from evoagent.skills.source_verification import SkillSourceVerifier
from evoagent.skills.trials import TrialScope


class MergeParent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    skill_id: UUID
    version_id: UUID
    content_hash: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
    lock_version: int = Field(ge=0)


class MergeProposal(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    workspace_id: UUID
    project_id: UUID | None = None
    parents: tuple[MergeParent, ...] = Field(min_length=2, max_length=4)
    definition: SkillDefinition
    client_request_id: str = Field(min_length=1, max_length=128)
    reason: str = Field(min_length=1, max_length=1000)


def merge_extraction_key(source_key, definition_hash):
    return (
        "merge:" + content_hash({"source_key": source_key, "definition_hash": definition_hash})[7:]
    )


class SkillMergeService:
    def __init__(self, factory, validator, settings):
        self.factory, self.validator, self.settings = factory, validator, settings

    async def _parents(self, session, payload):
        proofs = {}
        for parent in sorted(payload.parents, key=lambda item: str(item.skill_id)):
            version = await session.get(SkillVersionRecord, parent.version_id)
            skill = await session.get(SkillRecord, parent.skill_id)
            if (
                version is None
                or skill is None
                or version.skill_id != skill.id
                or version.content_hash != parent.content_hash
                or skill.lock_version != parent.lock_version
            ):
                raise ConcurrentUpdateError("merge_parent_version_conflict")
            # First slice requires an identical maximum scope. No implicit
            # widening of a project method into a workspace method.
            if skill.project_id != payload.project_id:
                raise LearningError("merge_scope_mismatch")
            parent_proofs = {}
            try:
                await SkillAccessPolicy().check(
                    session,
                    version.id,
                    workspace_id=payload.workspace_id,
                    project_id=payload.project_id,
                    source_proofs=parent_proofs,
                )
            except SkillAccessError as error:
                raise LearningError("merge_parent_source_invalid") from error
            for identity, proof in parent_proofs.items():
                if identity in proofs and proofs[identity] != proof:
                    raise LearningError("merge_source_identity_conflict")
                proofs[identity] = proof
        if not proofs or len(proofs) > 200:
            raise LearningError("merge_source_budget_exceeded")
        if any(proof.source_kind != "personal" for proof in proofs.values()):
            raise LearningError("merge_personal_sources_required")
        return proofs

    async def propose(self, payload: MergeProposal):
        if not self.settings.learning_enabled:
            raise LearningError("learning_disabled")
        if len({p.skill_id for p in payload.parents}) != len(payload.parents):
            raise LearningError("merge_distinct_skills_required")
        if not payload.reason.strip():
            raise LearningError("merge_reason_required")
        definition = payload.definition.model_dump(mode="json")
        body = payload.model_dump(mode="json", exclude={"client_request_id"})
        if detect_sensitive(canonical_json(body)):
            raise LearningError("sensitive_merge_payload")
        result = self.validator.validate(payload.definition)
        require_s6_annotations(payload.definition)
        async with self.factory() as session:
            initial = await self._parents(session, payload)
        # Storage and scanning never hold the control-plane transaction.
        verifier = SkillSourceVerifier(
            self.factory, self.settings, TrialScope(payload.workspace_id, payload.project_id)
        )
        try:
            async with asyncio.timeout(10):
                for parent in payload.parents:
                    await verifier.verify(parent.version_id)
        except (MemoryError, TimeoutError) as error:
            raise LearningError("merge_source_verification_failed") from error
        async with self.factory() as session:
            # Same Run -> Workspace order as source revocation/feedback.
            for run_id in sorted({p.run_id for p in initial.values()}, key=str):
                await session.execute(
                    update(RunRecord)
                    .where(RunRecord.id == run_id)
                    .values(next_feedback_revision=RunRecord.next_feedback_revision)
                )
            await session.execute(
                update(WorkspaceRecord)
                .where(WorkspaceRecord.id == payload.workspace_id)
                .values(name=WorkspaceRecord.name)
            )
            for parent in sorted(payload.parents, key=lambda item: str(item.skill_id)):
                await session.scalar(
                    select(SkillRecord).where(SkillRecord.id == parent.skill_id).with_for_update()
                )
            current = await self._parents(session, payload)
            if current != initial:
                raise LearningError("merge_sources_changed_during_scan")
            learning = LearningService(self.factory, learning_enabled=True)
            policy = await learning._policy(session, payload.workspace_id)
            if policy["mode"] == "off":
                raise LearningError("learning_policy_off")
            sources = []
            identities = {}
            for proof in sorted(current.values(), key=lambda item: str(item.artifact_id)):
                source = await session.get(LearningSourceRecord, proof.learning_source_id)
                await PersonalSourceService(
                    self.factory, max_source_risk=policy["max_source_risk"]
                ).check_in_session(
                    session,
                    source.run_id,
                    source.feedback_id,
                    "feedback" if source.feedback_id else "manual",
                )
                identity = (source.id, source.artifact_id, source.content_hash)
                if source.run_id in identities and identities[source.run_id] != identity:
                    raise LearningError("merge_source_identity_conflict")
                identities[source.run_id] = identity
                sources.append(source)
            primary = min(sources, key=lambda item: str(item.run_id))
            feedback = (
                await session.get(RunFeedbackRecord, primary.feedback_id)
                if primary.feedback_id
                else None
            )
            snapshot = {
                **policy,
                "validation_mode": "static_only",
                "source_policy_version": "personal:v1",
                "generation_origin": "local-user",
                "merge_contract": "merge:v1",
                "lineage": [
                    p.model_dump(mode="json")
                    for p in sorted(payload.parents, key=lambda p: str(p.skill_id))
                ],
                "definition_hash": content_hash(definition),
                "source_identities": [
                    {
                        "source_id": str(s.id),
                        "source_hash": s.content_hash,
                        "source_revision": s.source_revision,
                        "revocation_epoch": s.revocation_epoch,
                    }
                    for s in sources
                ],
            }
            frozen = {
                "run_id": str(primary.run_id),
                "source_revision": primary.source_revision,
                "learning_revision": feedback.learning_revision if feedback else 0,
                "learning_payload_hash": feedback.learning_payload_hash
                if feedback
                else content_hash({}),
                "target_skill_id": None,
                "base_version_id": None,
                "policy_hash": content_hash(snapshot),
                "evidence_manifest_hash": primary.evidence_manifest["evidence_manifest_hash"],
            }
            request = await LearningRepository(session).append_request(
                workspace_id=payload.workspace_id,
                origin_run_id=primary.run_id,
                client_request_id=payload.client_request_id,
                kind="propose",
                frozen_inputs=frozen,
                policy_snapshot=snapshot,
                request_body=body,
                trigger="merge",
                project_id=payload.project_id,
            )
            if request.candidate_version_id is not None:
                await session.commit()
                return request_view(request)
            if await session.scalar(
                select(SkillRecord.id).where(
                    SkillRecord.workspace_id == payload.workspace_id,
                    SkillRecord.slug == payload.definition.name,
                )
            ):
                raise LearningError("merge_new_slug_required")
            skill = SkillRecord(
                workspace_id=payload.workspace_id,
                project_id=payload.project_id,
                slug=payload.definition.name,
                name=payload.definition.name,
                description=payload.definition.description,
                next_version_number=2,
                next_event_sequence=2,
            )
            session.add(skill)
            await session.flush()
            version = SkillVersionRecord(
                skill_id=skill.id,
                version=1,
                schema_version=payload.definition.schema_version,
                definition=definition,
                content_hash=content_hash(definition),
                extraction_key=merge_extraction_key(request.source_key, content_hash(definition)),
                lifecycle_status=SkillVersionStatus.DRAFT,
            )
            session.add(version)
            await session.flush()
            for source in {s.run_id: s for s in sources}.values():
                session.add(
                    SkillSourceRecord(
                        skill_version_id=version.id,
                        source_run_id=source.run_id,
                        source_kind="personal",
                        learning_source_id=source.id,
                        trace_artifact_id=source.artifact_id,
                        source_trace_hash=source.content_hash,
                    )
                )
            report = {
                "schema_version": 1,
                "validation_mode": "static_only",
                "candidate_version_id": str(version.id),
                "candidate_hash": version.content_hash,
                "source_id": str(primary.id),
                "source_hash": primary.content_hash,
                "static_validation": {
                    "passed": True,
                    "ordered_step_ids": list(result.ordered_step_ids),
                },
                "task_validation": {"status": "not_run"},
                "trial_eligible": False,
            }
            session.add(
                SkillEventRecord(
                    skill_id=skill.id,
                    sequence=1,
                    event_type="skill.version_drafted",
                    payload={
                        "skill_version_id": str(version.id),
                        "source": "manual_merge",
                        "learning_request_id": str(request.id),
                    },
                    created_at=datetime.now(UTC),
                )
            )
            request.candidate_version_id = version.id
            request.validation_report, request.validation_report_hash = report, content_hash(report)
            request.status, request.stage = "ready_for_review", "review"
            await RunEventRepository(session).append(
                run_id=primary.run_id,
                event_type="learning.merge_proposed",
                payload={
                    "request_id": str(request.id),
                    "candidate_version_id": str(version.id),
                    "actor": "local-user",
                    "reason": payload.reason.strip(),
                    "parent_version_ids": [str(p.version_id) for p in payload.parents],
                },
                created_at=datetime.now(UTC),
            )
            await session.commit()
            return request_view(request)
