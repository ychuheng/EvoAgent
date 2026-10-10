"""Bounded durable health checks; paid learning never controls the safety brake."""

from datetime import timedelta

from sqlalchemy import exists, func, select

from evoagent.db.models import (
    ArtifactRecord,
    RunFeedbackRecord,
    RunRecord,
    RunSkillSelectionRecord,
    SessionRecord,
    SkillObservationRecord,
    SkillTrialRecord,
    SkillVersionRecord,
    TaskRecord,
)
from evoagent.learning.schema import LearningError
from evoagent.privacy.redaction import POLICY_VERSION
from evoagent.projects.inputs import InputSet
from evoagent.runtime.run_config import RunConfigSnapshot
from evoagent.skills.canonical import content_hash
from evoagent.skills.health import TrialHealth, evaluate_health, persisted_health_observations
from evoagent.skills.observation_evidence import OBSERVABLE_TERMINAL_STATES, HumanSkillObservation
from evoagent.skills.trials import SkillTrialService, TrialError, TrialScope
from evoagent.tasks.lease_guard import database_now


class SkillUsageService:
    def __init__(self, factory):
        self.factory = factory

    async def suggest_revision(self, version_id, scope):
        from evoagent.skills.revision_signals import SkillRevisionSignals

        return await SkillRevisionSignals(self.factory).suggest_revision(version_id, scope)

    async def summarize(self, skill_id, scope, since=None):
        from evoagent.skills.usage_reports import SkillUsageReports

        return await SkillUsageReports(self.factory).summarize(skill_id, scope, since)

    async def list_evidence(self, version_id, scope, *, cursor=None, limit=50):
        from evoagent.skills.usage_reports import SkillUsageReports

        return await SkillUsageReports(self.factory).list_evidence(
            version_id, scope, cursor=cursor, limit=limit
        )

    @staticmethod
    def pending_projection_query():
        latest_revision = (
            select(func.max(RunFeedbackRecord.revision))
            .where(RunFeedbackRecord.run_id == RunRecord.id)
            .correlate(RunRecord)
            .scalar_subquery()
        )
        projected = exists(
            select(SkillObservationRecord.id)
            .where(
                SkillObservationRecord.run_id == RunRecord.id,
                SkillObservationRecord.version_id == RunSkillSelectionRecord.skill_version_id,
                SkillObservationRecord.feedback_revision == func.coalesce(latest_revision, 0),
            )
            .correlate(RunRecord, RunSkillSelectionRecord)
        )
        return (
            select(RunRecord)
            .join(RunSkillSelectionRecord, RunSkillSelectionRecord.run_id == RunRecord.id)
            .where(
                RunSkillSelectionRecord.origin == "trial",
                RunSkillSelectionRecord.selection_policy_version == "skill-selector-v1",
                RunRecord.data_role == "personal",
                RunRecord.status.in_(OBSERVABLE_TERMINAL_STATES),
                RunRecord.ended_at.is_not(None),
                ~projected,
            )
        )

    async def scan_pending_observations(self, *, after_id=None, limit=50):
        from evoagent.skills.observation_jobs import schedule_observation

        if type(limit) is not int or not 1 <= limit <= 100:
            raise LearningError("observation_scan_bound_invalid")
        async with self.factory() as session:
            query = self.pending_projection_query()
            if after_id is not None:
                query = query.where(RunRecord.id > after_id)
            runs = tuple(
                await session.scalars(
                    query.order_by(RunRecord.id).limit(limit).with_for_update(skip_locked=True)
                )
            )
            for run in runs:
                revision = await session.scalar(
                    select(func.max(RunFeedbackRecord.revision)).where(
                        RunFeedbackRecord.run_id == run.id
                    )
                )
                await schedule_observation(session, run, revision or 0)
            await session.commit()
        return tuple(run.id for run in runs), runs[-1].id if len(runs) == limit else None

    async def collect(self, run_id, feedback_revision=0, *, job_guard=None):
        """Append one projection of verified actual trial use; unknown stays unknown."""
        if type(feedback_revision) is not int or feedback_revision < 0:
            raise LearningError("observation_revision_invalid")
        async with self.factory() as session:
            run = await session.scalar(
                select(RunRecord).where(RunRecord.id == run_id).with_for_update()
            )
            if run is None:
                raise LearningError("run_not_found")
            if job_guard is not None:
                if job_guard.run_id != run_id or job_guard.revision != feedback_revision:
                    raise LearningError("observation_job_fenced")
                await job_guard.check(session)
            if str(run.status) not in OBSERVABLE_TERMINAL_STATES or run.ended_at is None:
                raise LearningError("observation_terminal_run_required")
            # Historical selection rows and experimental targets are not proof.
            if not run.config_snapshot or run.config_snapshot.get("schema_version") != 3:
                return ()
            try:
                config = RunConfigSnapshot.model_validate(run.config_snapshot)
            except ValueError:
                raise LearningError("observation_selection_invalid") from None
            if config.content_hash() != run.config_hash:
                raise LearningError("observation_selection_invalid")
            chosen = config.selected_skills
            if not chosen or chosen[0].origin != "trial" or run.data_role != "personal":
                return ()
            selected = chosen[0]
            binding = await session.scalar(
                select(RunSkillSelectionRecord).where(RunSkillSelectionRecord.run_id == run_id)
            )
            task = await session.get(TaskRecord, run.task_id)
            if task is None:
                raise LearningError("observation_selection_invalid")
            chat = await session.get(SessionRecord, task.session_id)
            trial = await session.get(SkillTrialRecord, selected.trial_id)
            version = await session.get(SkillVersionRecord, selected.version_id)
            if (
                binding is None
                or chat is None
                or trial is None
                or version is None
                or binding.origin != "trial"
                or binding.trial_id != selected.trial_id
                or binding.skill_version_id != selected.version_id
                or binding.content_hash != selected.content_hash
                or binding.rendered_hash != selected.rendered_hash
                or binding.selection_policy_version != config.selector_version
                or binding.applicability.get("status") != "applicable"
                or str(binding.mode) != "retrieval"
                or binding.rank != 1
                or binding.scope_key != content_hash(selected.scope.model_dump(mode="json"))
                or selected.scope.workspace_id != chat.workspace_id
                or selected.scope.project_id != task.project_id
                or trial.workspace_id != chat.workspace_id
                or trial.project_id not in (None, task.project_id)
                or trial.version_id != version.id
                or trial.skill_id != version.skill_id
                or trial.scope_key != TrialScope(chat.workspace_id, trial.project_id).key
                or content_hash(version.definition) != selected.content_hash
            ):
                raise LearningError("observation_selection_invalid")
            existing = await session.scalar(
                select(SkillObservationRecord).where(
                    SkillObservationRecord.run_id == run_id,
                    SkillObservationRecord.version_id == selected.version_id,
                    SkillObservationRecord.feedback_revision == feedback_revision,
                )
            )
            if existing is not None:
                identifier = existing.id
            else:
                feedback = (
                    await session.scalar(
                        select(RunFeedbackRecord).where(
                            RunFeedbackRecord.run_id == run_id,
                            RunFeedbackRecord.revision == feedback_revision,
                        )
                    )
                    if feedback_revision
                    else None
                )
                if feedback_revision and feedback is None:
                    raise LearningError("observation_feedback_not_found")
                fingerprint = ""
                if task.frozen_inputs is not None:
                    try:
                        inputs = InputSet.model_validate(task.frozen_inputs)
                    except ValueError:
                        raise LearningError("observation_input_identity_invalid") from None
                    fingerprint = content_hash(
                        {
                            "family": task.family,
                            "goal": task.goal,
                            "inputs": inputs.model_dump(mode="json"),
                        }
                    )
                outcome, attribution, evidence = await self._human_evidence(
                    session, run, version, feedback, fingerprint
                )
                observation = SkillObservationRecord(
                    run_id=run_id,
                    version_id=version.id,
                    trial_id=trial.id,
                    selection_id=binding.id,
                    feedback_revision=feedback_revision,
                    outcome=outcome,
                    attribution=attribution,
                    evidence=evidence,
                    first_finished_at=run.ended_at,
                    input_fingerprint=fingerprint,
                )
                session.add(observation)
                await session.flush()
                identifier = observation.id
            await session.commit()
        # Locks on Run are released before acquiring Workspace/Skill locks.
        await self.evaluate_trial_health(trial.id)
        return (identifier,)

    @staticmethod
    async def _human_evidence(session, run, version, feedback, fingerprint):
        unknown = ("unknown", "uncertain", {"verification_origin": "unknown"})
        if feedback is None or feedback.actor_id != "local-user" or not fingerprint:
            return unknown
        entries = [ref for ref in feedback.evidence_refs if ref.get("type") == "skill_observation"]
        if len(entries) != 1:
            return unknown
        try:
            claim = HumanSkillObservation.model_validate(entries[0])
        except ValueError:
            return unknown
        known_steps = {item["id"] for item in version.definition.get("steps", ())}
        if claim.version_id != version.id or not set(claim.associated_steps) <= known_steps:
            return unknown
        for reference in claim.artifacts:
            artifact = await session.get(ArtifactRecord, reference.artifact_id)
            if (
                artifact is None
                or artifact.run_id != run.id
                or artifact.content_hash != reference.content_hash
                or artifact.redaction_status != "verified"
                or artifact.redaction_policy_version != POLICY_VERSION
                or artifact.redaction_checked_hash != artifact.content_hash
                or artifact.attributes.get("erased")
            ):
                return unknown
        return (
            claim.outcome,
            claim.attribution,
            {
                "verification_origin": "user",
                "actor": "local-user",
                "criterion_id": claim.criterion_id,
                "associated_steps": list(claim.associated_steps),
                "evidence_refs": [str(item.artifact_id) for item in claim.artifacts],
                "feedback_id": str(feedback.id),
            },
        )

    async def health_snapshot(self, session, trial):
        """Compute in caller transaction without another connection or side effects."""
        if trial.status != "active":
            return TrialHealth(trial.status)
        if content_hash(trial.health_policy_snapshot) != trial.health_policy_hash:
            return TrialHealth("health_pending")
        pending = await session.scalar(
            self.pending_projection_query()
            .where(
                RunSkillSelectionRecord.trial_id == trial.id,
                RunSkillSelectionRecord.skill_version_id == trial.version_id,
            )
            .limit(1)
        )
        if pending is not None:
            return TrialHealth("health_pending")
        now = await database_now(session)
        records = list(
            await session.scalars(
                select(SkillObservationRecord)
                .where(
                    SkillObservationRecord.trial_id == trial.id,
                    SkillObservationRecord.version_id == trial.version_id,
                    SkillObservationRecord.first_finished_at >= now - timedelta(hours=24),
                )
                .order_by(
                    SkillObservationRecord.first_finished_at.desc(),
                    SkillObservationRecord.id.desc(),
                )
                .limit(201)
            )
        )
        version = await session.get(SkillVersionRecord, trial.version_id)
        known_steps = {item["id"] for item in version.definition.get("steps", ())}
        return evaluate_health(
            persisted_health_observations(records, trial=trial, known_steps=known_steps),
            trial_id=trial.id,
            version_id=trial.version_id,
            now=now,
            policy=trial.health_policy_snapshot,
        )

    async def evaluate_trial_health(self, trial_id):
        trials = SkillTrialService(self.factory)
        if await trials.suspend_unavailable(trial_id):
            return TrialHealth("suspended")
        async with self.factory() as session:
            trial = await session.get(SkillTrialRecord, trial_id)
            if trial.status != "active":
                return TrialHealth(trial.status)
            decision = await self.health_snapshot(session, trial)
            policy_hash = trial.health_policy_hash
        if decision.status == "suspend":
            try:
                changed = await trials.auto_suspend(
                    trial_id,
                    policy_hash=policy_hash,
                    observation_ids=decision.evidence_ids,
                    reason="verified_skill_failures",
                )
            except TrialError as error:
                if str(error) != "trial_health_evidence_conflict":
                    raise
                # A new feedback revision arrived between reading and locking.
                # Never claim healthy until the next bounded recomputation.
                return TrialHealth("health_pending")
            if changed:
                return TrialHealth(
                    "suspended", decision.evidence_ids, decision.consecutive_failures
                )
            return TrialHealth("health_pending")
        return decision

    async def scan_trial_health(self, *, after_id=None, limit=50):
        """Keyset pages prevent an always-active first page starving later trials."""
        if type(limit) is not int or not 1 <= limit <= 100:
            raise TrialError("trial_health_scan_bound_invalid")
        async with self.factory() as session:
            query = select(SkillTrialRecord.id).where(SkillTrialRecord.status == "active")
            if after_id is not None:
                query = query.where(SkillTrialRecord.id > after_id)
            identifiers = tuple(
                await session.scalars(query.order_by(SkillTrialRecord.id).limit(limit))
            )
        results = []
        for identifier in identifiers:
            results.append((identifier, await self.evaluate_trial_health(identifier)))
        return tuple(results), identifiers[-1] if len(identifiers) == limit else None
