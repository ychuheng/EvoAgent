"""Short transactional feedback/request control; no model or executable tool dependency."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import and_, case, func, select, update

from evoagent.db.learning_accounting import unknown_usage as _unknown_usage
from evoagent.db.learning_accounting import unresolved_usage as _unresolved_usage
from evoagent.db.models import (
    ArtifactRecord,
    LearningPolicyRecord,
    LearningRequestAliasRecord,
    LearningRequestRecord,
    LearningSourceRecord,
    LearningSpendReservationRecord,
    MaintenanceJobRecord,
    RunFeedbackRecord,
    RunRecord,
    SessionRecord,
    SkillRecord,
    SkillVersionRecord,
    TaskRecord,
    WorkspaceRecord,
)
from evoagent.db.repositories.base import ConcurrentUpdateError
from evoagent.db.repositories.events import RunEventRepository
from evoagent.learning.planner import route_feedback
from evoagent.learning.repository import LearningRepository
from evoagent.learning.schema import (
    FeedbackPayload,
    FeedbackView,
    LearningError,
    LearningPolicySubmission,
    LearningRequestView,
    LearningSourceView,
    LearningSubmission,
)
from evoagent.learning.sources import PersonalSourceService
from evoagent.privacy.redaction import detect_sensitive, redact_text
from evoagent.skills.canonical import canonical_json, content_hash
from evoagent.skills.schema import SkillDefinition

_POLICY_FIELDS = (
    "mode",
    "daily_limit_micros",
    "request_limit_micros",
    "daily_candidate_limit",
    "cooldown_seconds",
    "max_source_risk",
    "lock_version",
)


def request_view(row):
    actions = ()
    if row.status in {"queued", "running"}:
        actions = ("cancel",)
        if (
            row.request_kind == "validate"
            and row.status == "queued"
            and row.stage == "task_validate"
        ):
            actions = ("start_validation", "cancel")
    elif row.status == "ready_for_review":
        actions = ("judge_validation",) if row.request_kind == "validate" else ("review", "reject")
    elif row.status in {"failed", "waiting_budget"}:
        actions = ("retry",)
    elif row.request_kind == "propose" and row.status == "completed" and row.stage == "reviewed":
        actions = ("prepare_validation",)
    return LearningRequestView(
        id=row.id,
        workspace_id=row.workspace_id,
        origin_run_id=row.origin_run_id,
        request_kind=row.request_kind,
        status=row.status,
        stage=row.stage,
        lock_version=row.lock_version,
        candidate_version_id=row.candidate_version_id,
        validation_report_hash=row.validation_report_hash,
        error_code=row.error_code,
        available_actions=actions,
        policy_snapshot=row.policy_snapshot,
        validation_report=row.validation_report,
    )


class LearningService:
    def __init__(self, session_factory, *, learning_enabled=False, generator_configuration=None):
        self.factory = session_factory
        self.enabled = learning_enabled
        self.generator_configuration = generator_configuration

    async def _scope(self, session, run_id):
        run = await session.get(RunRecord, run_id)
        task = await session.get(TaskRecord, run.task_id) if run else None
        chat = await session.get(SessionRecord, task.session_id) if task else None
        if chat is None:
            raise LearningError("run_not_found")
        return run, task, chat

    async def policy(self, workspace_id):
        async with self.factory() as session:
            if await session.get(WorkspaceRecord, workspace_id) is None:
                raise LearningError("workspace_not_found")
            return await self._policy(session, workspace_id)

    async def _policy(self, session, workspace_id):
        row = await session.get(LearningPolicyRecord, workspace_id)
        if row is None:
            # Reading a default policy must not create a row or a write transaction.
            return {
                "mode": "off",
                "daily_limit_micros": None,
                "request_limit_micros": None,
                "daily_candidate_limit": 3,
                "cooldown_seconds": 86400,
                "max_source_risk": "R1",
                "lock_version": 0,
            }
        return {field: getattr(row, field) for field in _POLICY_FIELDS}

    async def update_policy(self, workspace_id, payload: LearningPolicySubmission):
        async with self.factory() as session:
            workspace = await session.scalar(
                select(WorkspaceRecord).where(WorkspaceRecord.id == workspace_id).with_for_update()
            )
            if workspace is None:
                raise LearningError("workspace_not_found")
            # An UPDATE also serializes writers on the SQLite contract path.
            await session.execute(
                update(WorkspaceRecord)
                .where(WorkspaceRecord.id == workspace_id)
                .values(name=WorkspaceRecord.name)
            )
            row = await session.get(LearningPolicyRecord, workspace_id)
            if (row.lock_version if row else 0) != payload.expected_lock_version:
                raise ConcurrentUpdateError("learning_policy_conflict")
            if row is None:
                row = LearningPolicyRecord(workspace_id=workspace_id)
                session.add(row)
            for field, value in payload.model_dump(exclude={"expected_lock_version"}).items():
                setattr(row, field, value)
            row.lock_version = payload.expected_lock_version + 1
            await session.flush()
            result = {field: getattr(row, field) for field in _POLICY_FIELDS}
            await session.commit()
            return result

    async def record_feedback(
        self, run_id, payload: FeedbackPayload, *, client_request_id, expected_revision=None
    ):
        if payload.learn_from_feedback and not self.enabled:
            raise LearningError("learning_disabled")
        async with self.factory() as session:
            await self._scope(session, run_id)
            row = await LearningRepository(session).append_feedback(
                run_id, client_request_id, payload, "local-user", expected_revision
            )
            from evoagent.skills.observation_jobs import schedule_observation

            run = await session.get(RunRecord, run_id)
            await schedule_observation(session, run, row.revision)
            routing = route_feedback(payload)
            request = None
            if routing == "method":
                try:
                    request = await self._append_proposal(
                        session,
                        run_id,
                        LearningSubmission(
                            client_request_id=f"feedback:{row.id}", feedback_id=row.id
                        ),
                        trigger="feedback",
                    )
                except LearningError as error:
                    if error.code != "learning_revision_target_ambiguous":
                        raise
                    routing = "clarify"
            result = FeedbackView(
                id=row.id,
                run_id=run_id,
                revision=row.revision,
                learning_revision=row.learning_revision,
                routing=routing,
                learning_request_id=request.id if request else None,
            )
            await session.commit()
            return result

    async def request_learning(self, run_id: UUID, payload: LearningSubmission):
        if not self.enabled:
            raise LearningError("learning_disabled")
        async with self.factory() as session:
            result = request_view(await self._append_proposal(session, run_id, payload))
            await session.commit()
            return result

    async def _append_proposal(self, session, run_id, payload, *, trigger="manual"):
        run, task, chat = await self._scope(session, run_id)
        body = {
            "run_id": str(run_id),
            **payload.model_dump(mode="json", exclude={"client_request_id"}),
        }
        alias = await session.get(
            LearningRequestAliasRecord, (chat.workspace_id, payload.client_request_id)
        )
        if alias is not None:
            if alias.request_body_hash != content_hash(body):
                raise ConcurrentUpdateError("learning_request_conflict")
            return await session.get(LearningRequestRecord, alias.request_id)
        await session.execute(
            update(RunRecord)
            .where(RunRecord.id == run_id)
            .values(next_feedback_revision=RunRecord.next_feedback_revision)
        )
        await session.execute(
            update(WorkspaceRecord)
            .where(WorkspaceRecord.id == chat.workspace_id)
            .values(name=WorkspaceRecord.name)
        )
        policy = await self._policy(session, chat.workspace_id)
        if policy["mode"] == "off":
            raise LearningError("learning_policy_off")
        source_service = PersonalSourceService(
            self.factory, max_source_risk=policy["max_source_risk"]
        )
        eligibility = await source_service.check_in_session(
            session, run_id, payload.feedback_id, "feedback" if payload.feedback_id else "manual"
        )
        evidence = await source_service.build_evidence_in_session(
            session, run_id, payload.feedback_id
        )
        evidence_identity = evidence.model_dump(mode="json")
        # A comment-only feedback revision has a new row ID, but no new learning
        # semantics. Its identity must not create another candidate/job.
        for correction in evidence_identity["user_corrections"]:
            correction.pop("feedback_id", None)
        feedback = (
            await session.get(RunFeedbackRecord, payload.feedback_id)
            if payload.feedback_id
            else None
        )
        target_skill_id, base_version_id = payload.target_skill_id, payload.expected_base_version_id
        if (
            trigger == "feedback"
            and target_skill_id is None
            and feedback is not None
            and feedback.verdict in {"needs_fix", "incorrect"}
            and evidence.selected_versions
        ):
            if len(evidence.selected_versions) != 1:
                raise LearningError("learning_revision_target_ambiguous")
            base = await session.get(SkillVersionRecord, UUID(evidence.selected_versions[0]))
            skill = await session.get(SkillRecord, base.skill_id) if base else None
            if (
                skill is None
                or skill.workspace_id != chat.workspace_id
                or skill.project_id not in (None, task.project_id)
            ):
                raise LearningError("learning_revision_target_ambiguous")
            target_skill_id, base_version_id = skill.id, base.id
        snapshot = {
            **policy,
            "validation_mode": "static_only",
            "source_policy_version": "personal:v1",
            **(
                {"generator_configuration": self.generator_configuration}
                if self.generator_configuration is not None
                else {}
            ),
        }
        source_revision = content_hash(
            {
                "run_id": str(run_id),
                "feedback_learning_revision": feedback.learning_revision if feedback else 0,
                "feedback_learning_hash": feedback.learning_payload_hash if feedback else None,
                "project_authorization_version": eligibility.project_authorization_version,
                "source_policy_version": "personal:v1",
                "evidence_manifest_hash": content_hash(evidence_identity),
            }
        )
        inputs = {
            "run_id": str(run_id),
            "learning_revision": feedback.learning_revision if feedback else 0,
            "learning_payload_hash": feedback.learning_payload_hash
            if feedback
            else content_hash({}),
            "source_revision": source_revision,
            "target_skill_id": str(target_skill_id) if target_skill_id else None,
            "base_version_id": str(base_version_id) if base_version_id else None,
            "policy_hash": content_hash(snapshot),
            "feedback_id": str(payload.feedback_id) if payload.feedback_id else None,
            "evidence_manifest_hash": content_hash(evidence_identity),
        }
        row = await LearningRepository(session).append_request(
            workspace_id=chat.workspace_id,
            origin_run_id=run_id,
            client_request_id=payload.client_request_id,
            kind="propose",
            frozen_inputs=inputs,
            policy_snapshot=snapshot,
            request_body=body,
            trigger=trigger,
            project_id=task.project_id,
            target_skill_id=target_skill_id,
            base_version_id=base_version_id,
        )
        dedupe = f"learning:{row.id}:prepare:0"
        if not await session.scalar(
            select(MaintenanceJobRecord.id).where(MaintenanceJobRecord.dedupe_key == dedupe)
        ):
            session.add(
                MaintenanceJobRecord(
                    dedupe_key=dedupe,
                    kind="learning_propose",
                    learning_request_id=row.id,
                    payload={
                        "request_id": str(row.id),
                        "stage": "prepare",
                        "request_lock_version": row.lock_version,
                    },
                )
            )
            await session.flush()
        return row

    async def get_request(self, request_id):
        async with self.factory() as session:
            row = await session.get(LearningRequestRecord, request_id)
            if row is None:
                raise LearningError("learning_request_not_found")
            source = await session.scalar(
                select(LearningSourceRecord).where(
                    LearningSourceRecord.run_id == row.origin_run_id,
                    LearningSourceRecord.source_revision
                    == row.frozen_inputs.get("source_revision"),
                )
            )
            known, outstanding, unknown = (
                await session.execute(
                    select(
                        func.sum(
                            case(
                                (
                                    and_(
                                        LearningSpendReservationRecord.status == "settled",
                                        LearningSpendReservationRecord.actual_micros >= 0,
                                    ),
                                    LearningSpendReservationRecord.actual_micros,
                                ),
                                else_=0,
                            )
                        ),
                        func.sum(
                            case(
                                (
                                    _unresolved_usage(),
                                    LearningSpendReservationRecord.reserved_micros,
                                ),
                                else_=0,
                            )
                        ),
                        func.sum(case((_unknown_usage(), 1), else_=0)),
                    ).where(LearningSpendReservationRecord.request_id == row.id)
                )
            ).one()
            return request_view(row).model_copy(
                update={
                    "source": LearningSourceView.model_validate(source).model_dump(mode="json")
                    if source
                    else None,
                    "cost": {
                        "known_spent_micros": known or 0,
                        "outstanding_reserved_micros": outstanding or 0,
                        "unknown_usage_count": unknown or 0,
                    },
                }
            )

    async def list_requests(self, workspace_id, *, limit=50, cursor=None, status=None):
        if not 1 <= limit <= 100:
            raise LearningError("invalid_page_size")
        async with self.factory() as session:
            query = select(LearningRequestRecord).where(
                LearningRequestRecord.workspace_id == workspace_id
            )
            if cursor is not None:
                query = query.where(LearningRequestRecord.id > cursor)
            if status is not None:
                query = query.where(LearningRequestRecord.status == status)
            rows = list(
                await session.scalars(query.order_by(LearningRequestRecord.id).limit(limit + 1))
            )
            return {
                "items": [request_view(row) for row in rows[:limit]],
                "next_cursor": rows[limit - 1].id if len(rows) > limit else None,
            }

    async def _locked_request(self, session, request_id, expected):
        found = await session.get(LearningRequestRecord, request_id)
        if found is None:
            raise LearningError("learning_request_not_found")
        # Match feedback/request admission's Run -> Workspace -> Request order.
        await session.execute(
            update(RunRecord)
            .where(RunRecord.id == found.origin_run_id)
            .values(next_feedback_revision=RunRecord.next_feedback_revision)
        )
        await session.execute(
            update(WorkspaceRecord)
            .where(WorkspaceRecord.id == found.workspace_id)
            .values(name=WorkspaceRecord.name)
        )
        row = await session.scalar(
            select(LearningRequestRecord)
            .where(LearningRequestRecord.id == request_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if expected is not None and row.lock_version != expected:
            raise ConcurrentUpdateError("learning_request_version_conflict")
        return row

    async def _audit(self, session, row, action, reason):
        await RunEventRepository(session).append(
            run_id=row.origin_run_id,
            event_type=f"learning.{action}",
            payload={
                "request_id": str(row.id),
                "lock_version": row.lock_version,
                "actor": "local-user",
                "reason": redact_text(reason),
            },
            created_at=datetime.now(UTC),
        )

    async def cancel_request(self, request_id, expected_lock_version):
        async with self.factory() as session:
            row = await self._locked_request(session, request_id, expected_lock_version)
            if row.status not in {"queued", "running", "waiting_budget"}:
                raise LearningError("learning_request_not_cancellable")
            row.status = "cancelled"
            row.lock_version += 1
            await session.execute(
                update(MaintenanceJobRecord)
                .where(MaintenanceJobRecord.learning_request_id == row.id)
                .values(cancel_requested=True)
            )
            await session.execute(
                update(MaintenanceJobRecord)
                .where(
                    MaintenanceJobRecord.learning_request_id == row.id,
                    MaintenanceJobRecord.status.in_(("pending", "failed")),
                )
                .values(status="cancelled")
            )
            await self._audit(session, row, "cancelled", "user cancelled")
            result = request_view(row)
            await session.commit()
            return result

    async def review_candidate(self, request_id, action, expected_lock_version, reason):
        if action not in {"acknowledge", "reject"} or not reason.strip():
            raise LearningError("invalid_candidate_review")
        async with self.factory() as session:
            row = await self._locked_request(session, request_id, expected_lock_version)
            if (
                row.request_kind != "propose"
                or row.status != "ready_for_review"
                or row.candidate_version_id is None
            ):
                raise LearningError("candidate_not_ready_for_review")
            if action == "acknowledge":
                candidate = await session.get(SkillVersionRecord, row.candidate_version_id)
                skill = await session.get(SkillRecord, candidate.skill_id) if candidate else None
                if (
                    skill is None
                    or skill.workspace_id != row.workspace_id
                    or skill.project_id not in (None, row.project_id)
                    or (
                        row.target_skill_id is not None
                        and candidate.skill_id != row.target_skill_id
                    )
                    or candidate.parent_version_id != row.base_version_id
                    or content_hash(candidate.definition) != candidate.content_hash
                    or detect_sensitive(canonical_json(candidate.definition))
                ):
                    raise LearningError("candidate_identity_invalid")
                SkillDefinition.model_validate(candidate.definition)
                if (
                    row.validation_report is None
                    or content_hash(row.validation_report) != row.validation_report_hash
                    or not row.validation_report.get("static_validation", {}).get("passed")
                ):
                    raise LearningError("candidate_static_evidence_required")
                source = await session.scalar(
                    select(LearningSourceRecord).where(
                        LearningSourceRecord.run_id == row.origin_run_id,
                        LearningSourceRecord.source_revision
                        == row.frozen_inputs["source_revision"],
                    )
                )
                artifact = await session.get(ArtifactRecord, source.artifact_id) if source else None
                if (
                    source is None
                    or source.status != "valid"
                    or artifact is None
                    or artifact.attributes.get("erased")
                    or artifact.redaction_status == "quarantined"
                ):
                    raise LearningError("learning_source_revoked")
                await PersonalSourceService(
                    self.factory, max_source_risk=row.policy_snapshot["max_source_risk"]
                ).check_in_session(
                    session,
                    row.origin_run_id,
                    source.feedback_id,
                    "feedback" if source.feedback_id else "manual",
                )
            row.status = "completed" if action == "acknowledge" else "rejected"
            row.stage = "reviewed"
            row.lock_version += 1
            await self._audit(session, row, action, reason)
            result = request_view(row)
            await session.commit()
            return result

    async def reject_request(self, request_id, expected_lock_version, reason):
        return await self.review_candidate(request_id, "reject", expected_lock_version, reason)

    async def retry_request(self, request_id, expected_lock_version, client_request_id):
        if not self.enabled:
            raise LearningError("learning_disabled")
        async with self.factory() as session:
            row = await self._locked_request(session, request_id, None)
            body_hash = content_hash(
                {
                    "operation": "retry",
                    "request_id": str(request_id),
                    "expected_lock_version": expected_lock_version,
                }
            )
            alias = await session.get(
                LearningRequestAliasRecord, (row.workspace_id, client_request_id)
            )
            if alias is not None:
                if alias.request_id != row.id or alias.request_body_hash != body_hash:
                    raise ConcurrentUpdateError("learning_retry_conflict")
                return request_view(row)
            if row.lock_version != expected_lock_version:
                raise ConcurrentUpdateError("learning_request_version_conflict")
            if row.status not in {"failed", "waiting_budget"}:
                raise LearningError("learning_request_not_retryable")
            if await session.scalar(
                select(LearningSpendReservationRecord.id)
                .where(
                    LearningSpendReservationRecord.request_id == row.id,
                    _unresolved_usage(),
                )
                .limit(1)
            ):
                raise LearningError("learning_usage_unresolved")
            policy = await self._policy(session, row.workspace_id)
            if policy["mode"] == "off":
                raise LearningError("learning_policy_off")
            source_service = PersonalSourceService(
                self.factory,
                max_source_risk=min(
                    row.policy_snapshot["max_source_risk"], policy["max_source_risk"]
                ),
            )
            feedback_id = (
                UUID(row.frozen_inputs["feedback_id"])
                if row.frozen_inputs.get("feedback_id")
                else None
            )
            await source_service.check_in_session(
                session,
                row.origin_run_id,
                feedback_id,
                "feedback" if row.frozen_inputs.get("feedback_id") else "manual",
            )
            frozen_source = await session.scalar(
                select(LearningSourceRecord).where(
                    LearningSourceRecord.run_id == row.origin_run_id,
                    LearningSourceRecord.source_revision == row.frozen_inputs["source_revision"],
                )
            )
            if frozen_source is not None:
                artifact = await session.get(ArtifactRecord, frozen_source.artifact_id)
                if (
                    frozen_source.status != "valid"
                    or artifact is None
                    or artifact.attributes.get("erased")
                    or artifact.redaction_status == "quarantined"
                ):
                    raise LearningError("learning_source_revoked")
            else:
                evidence = await source_service.build_evidence_in_session(
                    session, row.origin_run_id, feedback_id
                )
                if source_service.evidence_identity_hash(evidence) != row.frozen_inputs.get(
                    "evidence_manifest_hash"
                ):
                    raise LearningError("learning_source_changed")
            row.status = "queued"
            row.error_code = None
            row.lock_version += 1
            session.add(
                LearningRequestAliasRecord(
                    workspace_id=row.workspace_id,
                    client_request_id=client_request_id,
                    request_id=row.id,
                    request_body_hash=body_hash,
                )
            )
            session.add(
                MaintenanceJobRecord(
                    dedupe_key=f"learning:{row.id}:{row.stage}:{row.lock_version}",
                    kind="learning_propose"
                    if row.request_kind == "propose"
                    else "learning_validate",
                    learning_request_id=row.id,
                    payload={
                        "request_id": str(row.id),
                        "stage": row.stage,
                        "request_lock_version": row.lock_version,
                        "retry_client_request_id_hash": content_hash(client_request_id),
                    },
                )
            )
            await self._audit(session, row, "retried", "explicit retry")
            result = request_view(row)
            await session.commit()
            return result
