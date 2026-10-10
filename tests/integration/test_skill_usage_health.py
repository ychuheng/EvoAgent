"""Persisted health triggers the brake without paid learning or model calls."""

from sqlalchemy import select

from evoagent.db.models import (
    SkillEventRecord,
    SkillObservationRecord,
    SkillRecord,
    SkillTrialRecord,
)
from evoagent.skills.lifecycle import SkillStatus
from evoagent.skills.usage import SkillUsageService
from tests.integration.test_personal_trials import failure_observations, seed_binding

pytest_plugins = ("tests.integration.test_personal_trials",)


async def test_health_service_suspends_and_restart_is_idempotent(trial_candidate):
    trial = await seed_binding(trial_candidate)
    _, db, _, _, _, _ = trial_candidate
    await failure_observations(trial_candidate, trial)
    decision = await SkillUsageService(db.session_factory).evaluate_trial_health(trial.id)
    assert decision.status == "suspended" and decision.consecutive_failures == 3
    assert len(decision.evidence_ids) == 3
    assert (await SkillUsageService(db.session_factory).evaluate_trial_health(trial.id)).status == (
        "suspended"
    )
    async with db.session_factory() as session:
        assert (
            len(
                list(
                    await session.scalars(
                        select(SkillEventRecord).where(
                            SkillEventRecord.event_type == "skill.trial_auto_suspended"
                        )
                    )
                )
            )
            == 1
        )


async def test_environment_failure_does_not_suspend(trial_candidate):
    trial = await seed_binding(trial_candidate)
    _, db, _, _, _, _ = trial_candidate
    await failure_observations(trial_candidate, trial, attribution="environment")
    result, cursor = await SkillUsageService(db.session_factory).scan_trial_health(limit=1)
    assert cursor == trial.id and result[0][1].status == "healthy"
    next_page, cursor = await SkillUsageService(db.session_factory).scan_trial_health(
        after_id=cursor
    )
    assert next_page == () and cursor is None


async def test_hard_revocation_uses_no_failure_threshold(trial_candidate):
    trial = await seed_binding(trial_candidate)
    _, db, _, _, _, skill = trial_candidate
    async with db.session_factory() as session:
        row = await session.get(SkillRecord, skill.id)
        row.status = SkillStatus.DISABLED
        await session.commit()
    assert (await SkillUsageService(db.session_factory).evaluate_trial_health(trial.id)).status == (
        "suspended"
    )
    async with db.session_factory() as session:
        row = await session.get(SkillTrialRecord, trial.id)
        assert row.suspension_reason == "skill_source_or_version_unavailable"


async def test_observation_backlog_is_pending_instead_of_healthy(trial_candidate):
    trial = await seed_binding(trial_candidate)
    _, db, _, _, _, _ = trial_candidate
    identifiers = await failure_observations(trial_candidate, trial)
    async with db.session_factory() as session:
        first = await session.get(SkillObservationRecord, identifiers[0])
        for revision in range(2, 200):
            session.add(
                SkillObservationRecord(
                    run_id=first.run_id,
                    version_id=first.version_id,
                    trial_id=first.trial_id,
                    selection_id=first.selection_id,
                    feedback_revision=revision,
                    outcome=first.outcome,
                    attribution=first.attribution,
                    evidence=first.evidence,
                    first_finished_at=first.first_finished_at,
                    input_fingerprint=first.input_fingerprint,
                )
            )
        await session.commit()
    assert (await SkillUsageService(db.session_factory).evaluate_trial_health(trial.id)).status == (
        "health_pending"
    )
