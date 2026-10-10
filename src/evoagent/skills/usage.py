"""Bounded durable health checks; paid learning never controls the safety brake."""

from datetime import timedelta

from sqlalchemy import select

from evoagent.db.models import SkillObservationRecord, SkillTrialRecord, SkillVersionRecord
from evoagent.skills.canonical import content_hash
from evoagent.skills.health import TrialHealth, evaluate_health, persisted_health_observations
from evoagent.skills.trials import SkillTrialService, TrialError
from evoagent.tasks.lease_guard import database_now


class SkillUsageService:
    def __init__(self, factory):
        self.factory = factory

    async def evaluate_trial_health(self, trial_id):
        trials = SkillTrialService(self.factory)
        if await trials.suspend_unavailable(trial_id):
            return TrialHealth("suspended")
        async with self.factory() as session:
            trial = await session.get(SkillTrialRecord, trial_id)
            if trial.status != "active":
                return TrialHealth(trial.status)
            if content_hash(trial.health_policy_snapshot) != trial.health_policy_hash:
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
            decision = evaluate_health(
                persisted_health_observations(records, trial=trial, known_steps=known_steps),
                trial_id=trial.id,
                version_id=trial.version_id,
                now=now,
                policy=trial.health_policy_snapshot,
            )
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
