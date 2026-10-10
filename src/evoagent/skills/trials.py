"""Explicit personal trial bindings; formal publication remains independent."""

from copy import deepcopy
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

from sqlalchemy import select, update

from evoagent.db.models import (
    EvalExperimentRecord,
    LearningRequestRecord,
    SkillObservationRecord,
    SkillTrialRecord,
    SkillVersionRecord,
    WorkspaceRecord,
)
from evoagent.db.repositories.skills import SkillRepository
from evoagent.learning.selection_evidence import adoption_contract_passed
from evoagent.privacy.redaction import detect_sensitive
from evoagent.skills.access import SkillAccessError, SkillAccessPolicy
from evoagent.skills.canonical import content_hash
from evoagent.skills.health import HEALTH_POLICY, evaluate_health, persisted_health_observations
from evoagent.skills.lifecycle import SkillStatus, SkillVersionStatus
from evoagent.tasks.lease_guard import database_now


class TrialError(ValueError):
    pass


@dataclass(frozen=True)
class TrialScope:
    workspace_id: UUID
    project_id: UUID | None = None

    @property
    def key(self):
        return f"workspace:{self.workspace_id}:project:{self.project_id or 'none'}"


@dataclass(frozen=True)
class TrialReadiness:
    ready: bool
    reasons: tuple[str, ...]
    report_hash: str | None = None


class SkillTrialService:
    def __init__(self, factory, *, trial_enabled=False):
        self.factory, self.enabled = factory, trial_enabled

    async def _lock_workspace(self, session, workspace_id):
        result = await session.execute(
            update(WorkspaceRecord)
            .where(WorkspaceRecord.id == workspace_id)
            .values(name=WorkspaceRecord.name)
        )
        if result.rowcount != 1:
            raise TrialError("trial_workspace_missing")

    async def assess(self, version_id, learning_request_id):
        async with self.factory() as session:
            return await self._assess(session, version_id, learning_request_id)

    async def _assess(self, session, version_id, request_id):
        request = await session.get(LearningRequestRecord, request_id)
        if request is None or request.request_kind != "validate":
            return TrialReadiness(False, ("independent_personal_validation_required",))
        version = await session.get(SkillVersionRecord, version_id)
        if (
            version is None
            or version.lifecycle_status is SkillVersionStatus.REJECTED
            or request.candidate_version_id != version_id
            or request.status not in {"ready_for_review", "completed"}
        ):
            return TrialReadiness(False, ("candidate_or_validation_state_invalid",))
        try:
            await SkillAccessPolicy().check(
                session,
                version_id,
                workspace_id=request.workspace_id,
                project_id=request.project_id,
            )
        except SkillAccessError:
            return TrialReadiness(False, ("candidate_source_unavailable",))
        report = request.validation_report
        if not isinstance(report, dict) or content_hash(report) != request.validation_report_hash:
            return TrialReadiness(False, ("validation_report_identity_invalid",))
        parent = (
            await session.get(LearningRequestRecord, request.parent_request_id)
            if request.parent_request_id
            else None
        )
        if (
            parent is None
            or parent.request_kind != "propose"
            or parent.candidate_version_id != version_id
            or not isinstance(parent.validation_report, dict)
            or content_hash(parent.validation_report) != parent.validation_report_hash
            or parent.validation_report.get("candidate_hash") != version.content_hash
            or parent.validation_report.get("static_validation", {}).get("passed") is not True
        ):
            return TrialReadiness(False, ("static_parent_report_required",))
        experiment = (
            await session.get(EvalExperimentRecord, request.validation_experiment_id)
            if request.validation_experiment_id
            else None
        )
        if (
            experiment is None
            or getattr(experiment, "purpose", "formal") != "personal_validation"
            or experiment.report_hash != report.get("execution_report_hash")
            or str(experiment.report_artifact_id) != report.get("execution_report_artifact_id")
            or report.get("validation_mode") != "personal_validation"
            or report.get("candidate_hash") != version.content_hash
            or report.get("candidate_version_id") != str(version_id)
            or report.get("input_manifest_hash")
            != request.frozen_inputs.get("validation_input_manifest_hash")
            or report.get("criteria_hash") != request.frozen_inputs.get("validation_criteria_hash")
        ):
            return TrialReadiness(False, ("independent_report_binding_invalid",))
        if (
            report.get("trial_eligible") is not True
            or report.get("cost", {}).get("provider") == "mock"
        ):
            return TrialReadiness(False, ("real_validation_and_adoption_pipeline_required",))
        from evoagent.learning.schema import LearningError
        from evoagent.learning.validation_evidence import verify_report_cost
        from evoagent.learning.validation_profiles import real_trial_eligible

        if (
            content_hash(request.policy_snapshot) != request.policy_hash
            or request.policy_hash != request.frozen_inputs.get("validation_policy_hash")
            or not real_trial_eligible(report, request.policy_snapshot)
        ):
            return TrialReadiness(False, ("real_validation_identity_invalid",))
        try:
            await verify_report_cost(session, request, report)
        except LearningError:
            return TrialReadiness(False, ("real_validation_payment_invalid",))
        items = report.get("items")
        if not isinstance(items, list) or not 2 <= len(items) <= 100:
            return TrialReadiness(False, ("positive_and_counterexample_required",))
        kinds = set()
        for item in items:
            if (
                not isinstance(item, dict)
                or item.get("verdict") != "pass"
                or item.get("judge_origin") not in {"machine", "user"}
                or not item.get("judge_id")
                or not item.get("criterion_id")
                or not item.get("evidence_refs")
                or not item.get("input_fingerprint")
                or item.get("independent_input") is not True
            ):
                return TrialReadiness(False, ("verified_independent_criteria_required",))
            kinds.add(item.get("case_kind"))
        if not {"positive", "counterexample"} <= kinds:
            return TrialReadiness(False, ("positive_and_counterexample_required",))
        if report.get("business_verification") != "passed":
            return TrialReadiness(False, ("business_verification_required",))
        if report.get("schema_version") != 2 or not adoption_contract_passed(items, version_id):
            return TrialReadiness(False, ("verified_actual_adoption_required",))
        return TrialReadiness(True, (), request.validation_report_hash)

    async def _event(self, session, skill_id, event_type, payload):
        from evoagent.db.models import SkillEventRecord

        repository = SkillRepository(session)
        session.add(
            SkillEventRecord(
                skill_id=skill_id,
                sequence=await repository.allocate_event_sequence(skill_id),
                event_type=event_type,
                payload=payload,
            )
        )

    async def activate(
        self, version_id, scope, request_id, expected_lock_version, reviewer, reason
    ):
        if not self.enabled:
            raise TrialError("trial_feature_not_ready")
        if (
            not reviewer
            or not reason
            or len(reviewer) > 128
            or len(reason) > 1000
            or detect_sensitive(reviewer)
            or detect_sensitive(reason)
        ):
            raise TrialError("trial_review_required")
        async with self.factory() as session:
            await self._lock_workspace(session, scope.workspace_id)
            version = await session.get(SkillVersionRecord, version_id)
            if version is None:
                raise TrialError("trial_version_missing")
            skill = await SkillRepository(session).get(version.skill_id, for_update=True)
            if (
                skill.lock_version != expected_lock_version
                or skill.status is not SkillStatus.ENABLED
                or skill.workspace_id != scope.workspace_id
                or skill.project_id not in (None, scope.project_id)
            ):
                raise TrialError("trial_scope_or_version_conflict")
            request = await session.get(LearningRequestRecord, request_id)
            if (
                request is None
                or request.workspace_id != scope.workspace_id
                or request.project_id != scope.project_id
            ):
                raise TrialError("trial_validation_scope_mismatch")
            readiness = await self._assess(session, version_id, request_id)
            if not readiness.ready:
                raise TrialError("trial_not_ready:" + readiness.reasons[0])
            previous = await session.scalar(
                select(SkillTrialRecord)
                .where(
                    SkillTrialRecord.skill_id == skill.id,
                    SkillTrialRecord.scope_key == scope.key,
                    SkillTrialRecord.status == "active",
                )
                .with_for_update()
            )
            if previous is not None:
                previous.status, previous.lock_version = "replaced", previous.lock_version + 1
                await session.flush()  # partial unique index before inserting the new binding
            trial = SkillTrialRecord(
                skill_id=skill.id,
                version_id=version_id,
                workspace_id=scope.workspace_id,
                project_id=scope.project_id,
                scope_key=scope.key,
                validation_request_id=request_id,
                report_hash=readiness.report_hash,
                health_policy_snapshot=deepcopy(HEALTH_POLICY),
                health_policy_hash=content_hash(HEALTH_POLICY),
                reviewer=reviewer,
                reason=reason,
            )
            session.add(trial)
            await session.flush()
            skill.lock_version += 1
            await self._event(
                session,
                skill.id,
                "skill.trial_activated",
                {
                    "trial_id": str(trial.id),
                    "version_id": str(version_id),
                    "scope_key": scope.key,
                    "validation_request_id": str(request_id),
                    "report_hash": readiness.report_hash,
                    "reviewer": reviewer,
                    "reason": reason,
                },
            )
            await session.commit()
            return trial

    async def _locked_trial(self, session, trial_id):
        found = await session.get(SkillTrialRecord, trial_id)
        if found is None:
            raise TrialError("trial_missing")
        await self._lock_workspace(session, found.workspace_id)
        skill = await SkillRepository(session).get(found.skill_id, for_update=True)
        trial = await session.scalar(
            select(SkillTrialRecord)
            .where(SkillTrialRecord.id == trial_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return skill, trial

    async def suspend(self, trial_id, expected_lock_version, reason):
        if not reason or len(reason) > 1000 or detect_sensitive(reason):
            raise TrialError("trial_suspension_reason_required")
        async with self.factory() as session:
            skill, trial = await self._locked_trial(session, trial_id)
            if trial.lock_version != expected_lock_version:
                raise TrialError("trial_version_conflict")
            if trial.status != "suspended":
                trial.status, trial.lock_version = "suspended", trial.lock_version + 1
                trial.suspended_at, trial.suspension_reason = await database_now(session), reason
                skill.lock_version += 1
                await self._event(
                    session,
                    skill.id,
                    "skill.trial_suspended",
                    {
                        "trial_id": str(trial.id),
                        "version_id": str(trial.version_id),
                        "reason": reason,
                        "actor": "local-user",
                    },
                )
            await session.commit()
            return trial

    async def suspend_unavailable(self, trial_id):
        """Hard source invalidation bypasses the three-failure health threshold."""
        async with self.factory() as session:
            skill, trial = await self._locked_trial(session, trial_id)
            if trial.status != "active":
                return False
            unavailable = skill.status is not SkillStatus.ENABLED
            version = await session.get(SkillVersionRecord, trial.version_id)
            unavailable |= (
                version is None or version.lifecycle_status is SkillVersionStatus.REJECTED
            )
            try:
                await SkillAccessPolicy().check(
                    session,
                    trial.version_id,
                    workspace_id=trial.workspace_id,
                    project_id=trial.project_id,
                )
            except SkillAccessError:
                unavailable = True
            if not unavailable:
                return False
            trial.status, trial.lock_version = "suspended", trial.lock_version + 1
            trial.suspended_at = await database_now(session)
            trial.suspension_reason = "skill_source_or_version_unavailable"
            skill.lock_version += 1
            await self._event(
                session,
                skill.id,
                "skill.trial_source_suspended",
                {
                    "trial_id": str(trial.id),
                    "version_id": str(trial.version_id),
                    "reason": trial.suspension_reason,
                },
            )
            await session.commit()
            return True

    async def rollback(self, trial_id, target_trial_id, expected_lock_version, reason):
        async with self.factory() as session:
            current = await session.get(SkillTrialRecord, trial_id)
            target = await session.get(SkillTrialRecord, target_trial_id)
            if (
                current is None
                or target is None
                or current.id == target.id
                or current.skill_id != target.skill_id
                or current.scope_key != target.scope_key
                or current.lock_version != expected_lock_version
            ):
                raise TrialError("trial_rollback_target_conflict")
            if target.status != "replaced":
                raise TrialError("trial_rollback_requires_fresh_validation")
            skill, locked = await self._locked_trial(session, trial_id)
            if locked.lock_version != expected_lock_version:
                raise TrialError("trial_version_conflict")
            # activate reacquires and checks the aggregate CAS; no unlocked
            # inference is used to adopt a different scope or changed version.
            skill_lock = skill.lock_version
            scope = TrialScope(target.workspace_id, target.project_id)
            target_version, validation_request = target.version_id, target.validation_request_id
        return await self.activate(
            target_version,
            scope,
            validation_request,
            skill_lock,
            "local-user",
            reason,
        )

    async def auto_suspend(self, trial_id, *, policy_hash, observation_ids, reason):
        if not reason or len(reason) > 1000 or detect_sensitive(reason):
            raise TrialError("trial_suspension_reason_required")
        async with self.factory() as session:
            skill, trial = await self._locked_trial(session, trial_id)
            if trial.status != "active":
                return False
            if (
                policy_hash != trial.health_policy_hash
                or content_hash(trial.health_policy_snapshot) != policy_hash
            ):
                raise TrialError("trial_health_policy_conflict")
            # Recompute inside the lock; caller-supplied IDs are never enough.
            cutoff = await database_now(session) - timedelta(hours=24)
            records = list(
                await session.scalars(
                    select(SkillObservationRecord)
                    .where(
                        SkillObservationRecord.trial_id == trial.id,
                        SkillObservationRecord.version_id == trial.version_id,
                        SkillObservationRecord.first_finished_at >= cutoff,
                    )
                    .order_by(SkillObservationRecord.first_finished_at.desc())
                    .limit(201)
                )
            )
            version = await session.get(SkillVersionRecord, trial.version_id)
            known_steps = (
                {item["id"] for item in version.definition.get("steps", ())} if version else set()
            )
            facts = persisted_health_observations(records, trial=trial, known_steps=known_steps)
            health = evaluate_health(
                facts,
                trial_id=trial.id,
                version_id=trial.version_id,
                now=await database_now(session),
                policy=trial.health_policy_snapshot,
            )
            if health.status != "suspend":
                return False
            if set(observation_ids) != set(health.evidence_ids):
                raise TrialError("trial_health_evidence_conflict")
            trial.status, trial.lock_version = "suspended", trial.lock_version + 1
            trial.suspended_at, trial.suspension_reason = await database_now(session), reason
            skill.lock_version += 1
            await self._event(
                session,
                skill.id,
                "skill.trial_auto_suspended",
                {
                    "trial_id": str(trial.id),
                    "version_id": str(trial.version_id),
                    "policy_hash": policy_hash,
                    "observation_ids": [str(item) for item in health.evidence_ids],
                    "threshold": trial.health_policy_snapshot["consecutive_failures"],
                    "reason": reason,
                },
            )
            await session.commit()
            return True

    async def list_for_scope(self, workspace_id, project_id=None, *, limit=50, offset=0):
        if not 1 <= limit <= 100 or offset < 0:
            raise TrialError("trial_page_invalid")
        async with self.factory() as session:
            return tuple(
                await session.scalars(
                    select(SkillTrialRecord)
                    .where(
                        SkillTrialRecord.workspace_id == workspace_id,
                        SkillTrialRecord.project_id == project_id,
                    )
                    .order_by(SkillTrialRecord.created_at.desc(), SkillTrialRecord.id)
                    .offset(offset)
                    .limit(limit)
                )
            )
