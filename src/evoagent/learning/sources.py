"""Personal-source admission is separate from completion and from paid extraction."""

from contextlib import suppress
from uuid import UUID, uuid4

from sqlalchemy import select, update

from evoagent.db.models import (
    ArtifactRecord,
    EvalRunRecord,
    LearningRequestRecord,
    LearningSourceRecord,
    MaintenanceJobRecord,
    ProjectRecord,
    RunEventRecord,
    RunFeedbackRecord,
    RunRecord,
    RunSkillSelectionRecord,
    RuntimeEvalRunRecord,
    SessionRecord,
    TaskRecord,
    ToolApprovalRecord,
    ToolCallRecord,
    ToolEffectRecord,
    WorkspaceRecord,
)
from evoagent.learning.schema import (
    ExperienceEvidence,
    LearningError,
    LearningSourceView,
    SourceEligibility,
)
from evoagent.memory.repository import check_run_references
from evoagent.memory.schema import MemoryError
from evoagent.privacy.artifact_access import ArtifactInjectionGuard, ArtifactSensitiveContent
from evoagent.privacy.redaction import POLICY_VERSION, detect_sensitive, redact_text, redact_value
from evoagent.skills.canonical import canonical_json, content_hash

_TERMINAL = frozenset({"completed", "failed", "cancelled", "timeout", "limit_reached"})
_MAX_ITEMS = 200


class PersonalSourceService:
    def __init__(self, session_factory, *, max_source_risk="R1", artifact_store=None):
        if max_source_risk not in {"R0", "R1", "R2", "R3"}:
            raise LearningError("invalid_source_risk_policy")
        self.factory = session_factory
        self.max_source_risk = max_source_risk
        self.store = artifact_store

    @staticmethod
    def evidence_identity_hash(evidence):
        body = evidence.model_dump(mode="json")
        for correction in body["user_corrections"]:
            correction.pop("feedback_id", None)
        return content_hash(body)

    async def _lock_run_scope(self, session, run_id):
        await session.execute(
            update(RunRecord)
            .where(RunRecord.id == run_id)
            .values(next_feedback_revision=RunRecord.next_feedback_revision)
        )
        workspace_id = await session.scalar(
            select(SessionRecord.workspace_id)
            .join(TaskRecord, TaskRecord.session_id == SessionRecord.id)
            .join(RunRecord, RunRecord.task_id == TaskRecord.id)
            .where(RunRecord.id == run_id)
        )
        if workspace_id is None:
            raise LearningError("source_run_not_found")
        await session.execute(
            update(WorkspaceRecord)
            .where(WorkspaceRecord.id == workspace_id)
            .values(name=WorkspaceRecord.name)
        )

    async def freeze(self, run_id, feedback_id, *, source_revision, job_guard):
        if self.store is None:
            raise LearningError("learning_source_store_unavailable")
        async with self.factory() as session:
            _, request = await job_guard.check(session)
            if (
                request.origin_run_id != run_id
                or request.frozen_inputs.get("source_revision") != source_revision
            ):
                raise LearningError("learning_source_changed")
            existing = await session.scalar(
                select(LearningSourceRecord).where(
                    LearningSourceRecord.run_id == run_id,
                    LearningSourceRecord.source_revision == source_revision,
                )
            )
            if existing is not None:
                if existing.status != "valid":
                    raise LearningError("learning_source_revoked")
                await self.check_in_session(
                    session,
                    run_id,
                    existing.feedback_id,
                    "feedback" if existing.feedback_id else "manual",
                )
                return LearningSourceView.model_validate(existing)
        evidence = await self.build_evidence(run_id, feedback_id)
        body = canonical_json(evidence.model_dump(mode="json"))
        import hashlib

        digest = "sha256:" + hashlib.sha256(body.encode()).hexdigest()
        identity_hash = self.evidence_identity_hash(evidence)
        async with self.factory() as session:
            _, request = await job_guard.check(session)
            if (
                request.origin_run_id != run_id
                or request.frozen_inputs.get("source_revision") != source_revision
                or request.frozen_inputs.get("evidence_manifest_hash") != identity_hash
            ):
                raise LearningError("learning_source_changed")
        # Run no transaction across storage or bounded scanning.
        await ArtifactInjectionGuard(session_factory=self.factory).verify_derived_text(
            text=body,
            run_id=run_id,
            source_id=f"learning:{source_revision}",
            source_hash=digest,
            purpose="learning_source_freeze",
        )
        stored = await self.store.write_unique(
            run_id, f"learning-source-{uuid4().hex}", body.encode()
        )
        registered = False
        commit_started = False
        try:
            async with self.factory() as session:
                await self._lock_run_scope(session, run_id)
                existing = await session.scalar(
                    select(LearningSourceRecord)
                    .where(
                        LearningSourceRecord.run_id == run_id,
                        LearningSourceRecord.source_revision == source_revision,
                    )
                    .with_for_update()
                )
                _, request = await job_guard.check(session)
                if (
                    request.origin_run_id != run_id
                    or request.frozen_inputs["source_revision"] != source_revision
                ):
                    raise LearningError("learning_source_changed")
                current = await self.build_evidence_in_session(session, run_id, feedback_id)
                if self.evidence_identity_hash(current) != identity_hash:
                    raise LearningError("learning_source_changed")
                if existing is not None:
                    if existing.status != "valid":
                        raise LearningError("learning_source_revoked")
                    return LearningSourceView.model_validate(existing)
                artifact = ArtifactRecord(
                    run_id=run_id,
                    type="learning_source",
                    uri=stored.uri,
                    content_hash=stored.content_hash,
                    size_bytes=stored.size_bytes,
                    attributes={"source_revision": source_revision},
                    redaction_status="verified",
                    redaction_policy_version=POLICY_VERSION,
                    redaction_checked_hash=stored.content_hash,
                )
                session.add(artifact)
                await session.flush()
                source = LearningSourceRecord(
                    run_id=run_id,
                    feedback_id=feedback_id,
                    source_revision=source_revision,
                    source_role="personal",
                    artifact_id=artifact.id,
                    content_hash=stored.content_hash,
                    parent_skill_versions=list(current.selected_versions),
                    evidence_manifest={
                        "evidence_manifest_hash": identity_hash,
                        "project_authorization_version": (
                            await session.get(
                                TaskRecord, (await session.get(RunRecord, run_id)).task_id
                            )
                        ).project_authorization_version,
                    },
                )
                session.add(source)
                await session.flush()
                result = LearningSourceView.model_validate(source)
                commit_started = True
                await session.commit()
                registered = True
                return result
        finally:
            # An ambiguous commit may have registered the immutable file. Keep
            # it for reconciliation rather than deleting durable source bytes.
            if not registered and not commit_started:
                with suppress(Exception):
                    await self.store.erase(stored.uri)

    async def read_frozen(self, source_id, *, expected_revocation_epoch=0):
        if self.store is None:
            raise LearningError("learning_source_store_unavailable")
        async with self.factory() as session:
            source = await session.get(LearningSourceRecord, source_id)
            if (
                source is None
                or source.status != "valid"
                or source.revocation_epoch != expected_revocation_epoch
            ):
                raise LearningError("learning_source_revoked")
            artifact = await session.get(ArtifactRecord, source.artifact_id)
            if (
                artifact is None
                or artifact.attributes.get("erased")
                or artifact.content_hash != source.content_hash
            ):
                raise LearningError("learning_source_artifact_invalid")
            if artifact.redaction_status == "quarantined":
                raise LearningError("learning_source_quarantined")
            run_id, feedback_id, uri, digest = (
                source.run_id,
                source.feedback_id,
                artifact.uri,
                source.content_hash,
            )
            await self.check_in_session(
                session, run_id, feedback_id, "feedback" if feedback_id else "manual"
            )
        # Dedicated reader: learning_source is deliberately NOT added to the
        # generic artifact_read whitelist. No original artifact re-reading.
        raw = await self.store.read_bounded(uri, max_bytes=128 * 1024)
        try:
            text = await ArtifactInjectionGuard(session_factory=self.factory).verify_derived_text(
                text=raw.decode("utf8"),
                run_id=run_id,
                source_id=str(source_id),
                source_hash=digest,
                purpose="learning_source_read",
            )
        except ArtifactSensitiveContent:
            async with self.factory() as session:
                await session.execute(
                    update(ArtifactRecord)
                    .where(
                        ArtifactRecord.id == source.artifact_id,
                        ArtifactRecord.content_hash == digest,
                    )
                    .values(redaction_status="quarantined")
                )
                await session.commit()
            raise
        async with self.factory() as session:
            source = await session.get(LearningSourceRecord, source_id)
            if (
                source is None
                or source.status != "valid"
                or source.revocation_epoch != expected_revocation_epoch
            ):
                raise LearningError("learning_source_revoked")
            artifact = await session.get(ArtifactRecord, source.artifact_id)
            if (
                artifact is None
                or artifact.attributes.get("erased")
                or artifact.content_hash != digest
                or artifact.redaction_status == "quarantined"
            ):
                raise LearningError("learning_source_artifact_invalid")
            await self.check_in_session(
                session, run_id, feedback_id, "feedback" if feedback_id else "manual"
            )
        return ExperienceEvidence.model_validate_json(text)

    async def revoke(self, source_id, reason, *, expected_status=None):
        if not reason.strip():
            raise LearningError("source_revocation_reason_required")
        async with self.factory() as session:
            initial = await session.get(LearningSourceRecord, source_id)
            if initial is None:
                raise LearningError("learning_source_not_found")
            await self._lock_run_scope(session, initial.run_id)
            source = await session.scalar(
                select(LearningSourceRecord)
                .where(
                    LearningSourceRecord.id == source_id,
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if expected_status is not None and source.status != expected_status:
                from evoagent.db.repositories.base import ConcurrentUpdateError

                raise ConcurrentUpdateError("learning_source_status_conflict")
            if source.status != "valid":
                return LearningSourceView.model_validate(source)
            source.status = "revoked"
            source.revocation_epoch += 1
            requests = list(
                await session.scalars(
                    select(LearningRequestRecord)
                    .where(
                        LearningRequestRecord.origin_run_id == source.run_id,
                        LearningRequestRecord.frozen_inputs["source_revision"].as_string()
                        == source.source_revision,
                    )
                    .order_by(LearningRequestRecord.id)
                    .with_for_update()
                )
            )
            for request in requests:
                if request.status in {"queued", "running", "ready_for_review", "failed"}:
                    request.status = "superseded"
                    request.error_code = "learning_source_revoked"
                    request.lock_version += 1
                    await session.execute(
                        update(MaintenanceJobRecord)
                        .where(
                            MaintenanceJobRecord.learning_request_id == request.id,
                            MaintenanceJobRecord.status.in_(("pending", "running", "failed")),
                        )
                        .values(cancel_requested=True)
                    )
                    await session.execute(
                        update(MaintenanceJobRecord)
                        .where(
                            MaintenanceJobRecord.learning_request_id == request.id,
                            MaintenanceJobRecord.status.in_(("pending", "failed")),
                        )
                        .values(status="cancelled")
                    )
            session.add(
                MaintenanceJobRecord(
                    dedupe_key=f"learning-source:{source.id}:revoke:{source.revocation_epoch}",
                    kind="learning_revoke",
                    payload={
                        "source_id": str(source.id),
                        "revocation_epoch": source.revocation_epoch,
                        "reason": redact_text(reason),
                    },
                )
            )
            from datetime import UTC, datetime

            from evoagent.db.repositories.events import RunEventRepository

            await RunEventRepository(session).append(
                run_id=source.run_id,
                event_type="learning.source_revoked",
                payload={
                    "source_id": str(source.id),
                    "revocation_epoch": source.revocation_epoch,
                    "actor": "local-user",
                    "reason": redact_text(reason),
                },
                created_at=datetime.now(UTC),
            )
            result = LearningSourceView.model_validate(source)
            await session.commit()
            return result

    async def check(self, run_id: UUID, feedback_id: UUID | None, purpose: str):
        async with self.factory() as session:
            return await self.check_in_session(session, run_id, feedback_id, purpose)

    async def check_in_session(self, session, run_id, feedback_id, purpose):
        if purpose not in {"manual", "feedback", "discover", "freeze", "read", "publish"}:
            raise LearningError("invalid_source_purpose")
        run = await session.get(RunRecord, run_id)
        if run is None:
            raise LearningError("source_run_not_found")
        task = await session.get(TaskRecord, run.task_id)
        chat = await session.get(SessionRecord, task.session_id) if task else None
        if chat is None:
            raise LearningError("source_scope_invalid")
        if run.data_role != "personal":
            raise LearningError("personal_source_role_required")
        for record in (EvalRunRecord, RuntimeEvalRunRecord):
            if await session.scalar(select(record.id).where(record.run_id == run_id).limit(1)):
                raise LearningError("evaluation_source_forbidden")
        if str(run.status) not in _TERMINAL:
            raise LearningError("source_run_not_terminal")
        if await session.scalar(
            select(ToolCallRecord.id)
            .where(
                ToolCallRecord.run_id == run_id,
                ToolCallRecord.status.in_(("unknown", "pending", "running")),
            )
            .limit(1)
        ):
            raise LearningError("source_tool_unresolved")
        if await session.scalar(
            select(ToolCallRecord.id)
            .where(ToolCallRecord.run_id == run_id, ToolCallRecord.risk > self.max_source_risk)
            .limit(1)
        ):
            raise LearningError("source_risk_exceeds_policy")
        project_version = None
        if task.project_id is not None:
            project = await session.get(ProjectRecord, task.project_id)
            if (
                project is None
                or project.workspace_id != chat.workspace_id
                or str(project.status) != "available"
                or project.authorization_version != task.project_authorization_version
            ):
                raise LearningError("source_project_unavailable")
            project_version = project.authorization_version
        if await session.scalar(
            select(ToolEffectRecord.id)
            .join(ToolCallRecord, ToolEffectRecord.tool_call_id == ToolCallRecord.id)
            .where(
                ToolCallRecord.run_id == run_id,
                ToolEffectRecord.status.in_(("unknown", "prepared", "executing")),
            )
            .limit(1)
        ):
            raise LearningError("source_effect_unresolved")
        if await session.scalar(
            select(ToolApprovalRecord.id)
            .where(ToolApprovalRecord.task_id == task.id, ToolApprovalRecord.status == "pending")
            .limit(1)
        ):
            raise LearningError("source_approval_pending")
        try:
            await check_run_references(session, run_id)
        except MemoryError as error:
            raise LearningError("source_reference_revoked") from error
        feedback = await session.get(RunFeedbackRecord, feedback_id) if feedback_id else None
        if feedback_id is not None and (feedback is None or feedback.run_id != run_id):
            raise LearningError("source_feedback_mismatch")
        authorized = purpose == "manual" and feedback is None
        if feedback is not None:
            authorized = feedback.intent == "method" and feedback.learn_from_feedback
        if not authorized:
            raise LearningError("source_method_consent_required")
        return SourceEligibility(
            run_id=run_id,
            workspace_id=chat.workspace_id,
            project_id=task.project_id,
            run_status=str(run.status),
            feedback_id=feedback_id,
            method_authorized=authorized,
            user_reported_helpful=feedback is not None and feedback.verdict == "helpful",
            project_authorization_version=project_version,
        )

    async def build_evidence(self, run_id: UUID, feedback_id: UUID | None):
        async with self.factory() as session:
            return await self.build_evidence_in_session(session, run_id, feedback_id)

    async def build_evidence_in_session(self, session, run_id, feedback_id):
        eligibility = await self.check_in_session(
            session, run_id, feedback_id, "feedback" if feedback_id else "manual"
        )
        run = await session.get(RunRecord, run_id)
        task = await session.get(TaskRecord, run.task_id)
        feedback = await session.get(RunFeedbackRecord, feedback_id) if feedback_id else None
        tools = list(
            await session.scalars(
                select(ToolCallRecord)
                .where(ToolCallRecord.run_id == run_id)
                .order_by(ToolCallRecord.created_at, ToolCallRecord.id)
                .limit(_MAX_ITEMS + 1)
            )
        )
        artifacts = list(
            await session.scalars(
                select(ArtifactRecord)
                .where(
                    ArtifactRecord.run_id == run_id,
                    ArtifactRecord.type.not_in(
                        (
                            "learning_source",
                            "application/vnd.evoagent.skill-source+json",
                            "application/vnd.evoagent.eval-report+json",
                        )
                    ),
                )
                .order_by(ArtifactRecord.id)
                .limit(_MAX_ITEMS + 1)
            )
        )
        selections = list(
            await session.scalars(
                select(RunSkillSelectionRecord)
                .where(RunSkillSelectionRecord.run_id == run_id)
                .order_by(RunSkillSelectionRecord.id)
                .limit(_MAX_ITEMS + 1)
            )
        )
        # Progress is never success evidence. Only recorded acceptance results
        # qualify as machine evidence; assertions in a final answer do not.
        checks = list(
            await session.scalars(
                select(RunEventRecord)
                .where(
                    RunEventRecord.run_id == run_id,
                    RunEventRecord.event_type == "acceptance.checked",
                )
                .order_by(RunEventRecord.sequence)
                .limit(_MAX_ITEMS + 1)
            )
        )
        if any(len(rows) > _MAX_ITEMS for rows in (tools, artifacts, selections, checks)):
            raise LearningError("source_evidence_item_budget_exceeded")
        manifest = tuple(
            {
                "tool_call_id": str(tool.id),
                "tool": tool.tool_name,
                "status": str(tool.status),
                "risk": tool.risk,
                "execution_binding_hash": content_hash(tool.execution_binding or {}),
            }
            for tool in tools
        )
        evidence = ExperienceEvidence(
            goal=task.goal,
            outcome={
                "run_status": str(run.status),
                "final_answer": run.final_answer,
                "final_answer_origin": "model_inference",
                "user_reported_helpful": eligibility.user_reported_helpful,
                "user_verdict": feedback.verdict if feedback else None,
            },
            verified_facts=tuple(
                {"origin": "machine", "event_sequence": row.sequence, "result": row.payload}
                for row in checks
            ),
            user_corrections=(
                (
                    {
                        "origin": "user",
                        "feedback_id": str(feedback.id),
                        "verdict": feedback.verdict,
                        "correction": feedback.correction,
                    },
                )
                if feedback
                else ()
            ),
            failed_attempts=tuple(item for item in manifest if item["status"] != "succeeded"),
            effective_steps=tuple(
                {**item, "origin": "machine", "proves": "tool_execution_only"}
                for item in manifest
                if item["status"] == "succeeded"
            ),
            selected_versions=tuple(str(row.skill_version_id) for row in selections),
            artifact_refs=tuple(
                {
                    "id": str(row.id),
                    "hash": row.content_hash,
                    "type": row.type,
                    "size_bytes": row.size_bytes,
                    "body_read": False,
                }
                for row in artifacts
                if not row.attributes.get("erased")
            ),
            input_refs=({"origin": "task_goal", "hash": content_hash(redact_text(task.goal))},)
            + tuple(
                {
                    "origin": "task_input_manifest",
                    "hash": "sha256:" + item["sha256"],
                    "size_bytes": item["size_bytes"],
                    "kind": item["kind"],
                }
                for item in (task.frozen_inputs or {}).get("files", [])
            ),
            tool_manifest_hash=content_hash(manifest),
            unknowns=("completion_is_not_business_success",) if not checks else (),
        )
        body = canonical_json(evidence.model_dump(mode="json"))
        if len(body.encode()) > 128 * 1024:
            raise LearningError("source_evidence_byte_budget_exceeded")
        # No original artifact reads, model calls, filesystem paths or tools.
        # Every text field is rechecked by the current shared primitive.
        original = evidence.model_dump(mode="json")
        safe = redact_value(original)
        safe.update(
            redaction_policy_version=POLICY_VERSION,
            redacted=safe != original,
            redaction_categories=detect_sensitive(body),
        )
        return ExperienceEvidence.model_validate(safe)
