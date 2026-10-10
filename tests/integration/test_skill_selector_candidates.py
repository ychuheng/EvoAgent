"""Candidate authority tests; synthetic gates are not real efficacy evidence."""

from uuid import uuid4

import pytest
from sqlalchemy import select

from evoagent.config import Settings
from evoagent.db.models import (
    EvalDatasetRecord,
    EvalExperimentRecord,
    LearningSourceRecord,
    SkillRecord,
    SkillTrialRecord,
    SkillVersionRecord,
)
from evoagent.evals.gates import GateReport
from evoagent.memory.schema import MemoryError
from evoagent.skills.applicability import VerifiedSkillFacts
from evoagent.skills.lifecycle import SkillVersionStatus
from evoagent.skills.selection import RankedSkill, SkillSelectionPlan, SkillSelector
from evoagent.skills.selection_snapshot import SkillSelectionScope
from tests.integration.test_personal_trials import seed_binding

pytest_plugins = ("tests.integration.test_personal_trials",)


async def formal_fixture(context):
    _, db, _, _, version, skill = context
    async with db.session_factory() as session:
        dataset = EvalDatasetRecord(
            name="synthetic-selector", version=1, content_hash="sha256:" + "b" * 64
        )
        session.add(dataset)
        await session.flush()
        experiment = EvalExperimentRecord(
            kind="skill_comparison",
            dataset_id=dataset.id,
            skill_version_id=version.id,
            status="completed",
            purpose="formal",
            config_snapshot={"synthetic": True},
            config_hash="sha256:" + "a" * 64,
        )
        session.add(experiment)
        await session.flush()
        report = GateReport(
            skill_version_id=version.id,
            experiment_id=experiment.id,
            passed=True,
            checks=(),
        )
        experiment.gate_report = report.model_dump(mode="json")
        experiment.gate_report_hash = report.report_hash()
        row = await session.get(SkillVersionRecord, version.id)
        row.lifecycle_status = SkillVersionStatus.ACTIVE
        row.gate_report_hash = report.report_hash()
        owner = await session.get(SkillRecord, skill.id)
        owner.active_version_id = version.id
        await session.commit()
    return experiment.id


def selector(context, scope=None):
    from evoagent.tools.registry import ToolRegistry

    _, db, _, _, _, skill = context
    instance = SkillSelector(
        db.session_factory,
        Settings(_env_file=None),
        ToolRegistry(),
        None,
        scope or SkillSelectionScope(workspace_id=skill.workspace_id),
    )
    return instance


async def test_formal_candidates_require_scope_source_and_formal_gate(trial_candidate):
    instance = selector(trial_candidate)
    assert await instance.candidates() == ()  # DRAFT is not a formal choice
    experiment_id = await formal_fixture(trial_candidate)
    loaded = await instance.candidates()
    assert len(loaded) == 1 and loaded[0].origin == "formal"
    assert await instance.candidates(run_mode="baseline") == ()
    foreign = selector(trial_candidate, SkillSelectionScope(workspace_id=uuid4()))
    assert await foreign.candidates() == ()
    with pytest.raises(MemoryError, match="internal_pin_required"):
        await instance.candidates(
            run_mode="pinned_skill", pinned_version_id=loaded[0].document.version_id
        )
    _, db, _, _, _, _ = trial_candidate
    async with db.session_factory() as session:
        await session.execute(
            EvalExperimentRecord.__table__.update()
            .where(EvalExperimentRecord.id == experiment_id)
            .values(purpose="personal_validation")
        )
        await session.commit()
    assert await instance.candidates() == ()


async def test_latest_trial_intent_blocks_formal_fallback_and_stale_freeze(trial_candidate):
    await formal_fixture(trial_candidate)
    instance = selector(trial_candidate)
    loaded = (await instance.candidates())[0]
    facts = VerifiedSkillFacts.from_runtime(instance.registry)
    decision = instance.assess(loaded.document.definition, facts)
    # Revalidate authority independently of the pure planner's applicability filtering.
    plan = SkillSelectionPlan(RankedSkill(loaded, 1, (), decision), None)
    _, db, _, _, _, _ = trial_candidate
    async with db.session_factory() as session:
        await instance.revalidate(session, plan)
    trial = await seed_binding(trial_candidate)
    async with db.session_factory() as session:
        row = await session.get(SkillTrialRecord, trial.id)
        row.status = "suspended"
        await session.commit()
    # Synthetic P2 report is not independent personal validation; no trial or formal fallback.
    assert await instance.candidates() == ()
    async with db.session_factory() as session:
        with pytest.raises(MemoryError, match="selection_changed"):
            await instance.revalidate(session, plan)


async def test_revoked_source_blocks_cached_candidate(trial_candidate):
    await formal_fixture(trial_candidate)
    instance = selector(trial_candidate)
    loaded = (await instance.candidates())[0]
    decision = instance.assess(
        loaded.document.definition, VerifiedSkillFacts.from_runtime(instance.registry)
    )
    plan = SkillSelectionPlan(RankedSkill(loaded, 1, (), decision), None)
    _, db, _, _, _, _ = trial_candidate
    async with db.session_factory() as session:
        source = await session.scalar(select(LearningSourceRecord))
        source.status = "revoked"
        await session.commit()
    assert await instance.candidates() == ()
    async with db.session_factory() as session:
        with pytest.raises(MemoryError, match="source_revoked"):
            await instance.revalidate(session, plan)
