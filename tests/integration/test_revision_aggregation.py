"""Consented synthetic failures aggregate complete provenance, never activate."""

import asyncio
from copy import deepcopy

import pytest
from sqlalchemy import select

from evoagent.db.models import (
    ArtifactRecord,
    LearningRequestRecord,
    LearningSourceRecord,
    RunFeedbackRecord,
    SkillObservationRecord,
    SkillRecord,
    SkillSourceRecord,
    SkillVersionRecord,
)
from evoagent.learning.discovery import CandidateDiscoveryService
from evoagent.learning.repository import LearningRepository
from evoagent.learning.schema import (
    FeedbackPayload,
    LearningPolicySubmission,
    LearningSubmission,
)
from evoagent.learning.service import LearningService
from evoagent.learning.sources import PersonalSourceService
from evoagent.skills.access import SkillAccessError, SkillAccessPolicy
from evoagent.skills.schema import SkillDefinition
from evoagent.trace.artifacts import LocalArtifactStore
from tests.integration.test_learning_worker import worker
from tests.integration.test_skill_revision_signals import failures

pytest_plugins = ("tests.integration.test_personal_trials",)


async def setup(context, *, consent=True, independent_files=True):
    db, version, observations = await failures(
        context,
        consent=consent,
        independent_files=independent_files,
    )
    settings = context[0]._transport.app.state.settings
    learning = LearningService(
        db.session_factory,
        learning_enabled=True,
        generator_configuration={"provider": "mock", "model": "mock"},
    )
    policy = await learning.policy(context[-1].workspace_id)
    await learning.update_policy(
        context[-1].workspace_id,
        LearningPolicySubmission(
            expected_lock_version=policy["lock_version"],
            mode="suggest",
            cooldown_seconds=0,
        ),
    )
    sources = []
    if consent:
        for observation_id in observations:
            async with db.session_factory() as session:
                obs = await session.get(SkillObservationRecord, observation_id)
                feedback = await session.scalar(
                    select(RunFeedbackRecord)
                    .where(RunFeedbackRecord.run_id == obs.run_id)
                    .order_by(RunFeedbackRecord.revision.desc())
                    .limit(1)
                )
            request = await learning.request_learning(
                obs.run_id,
                LearningSubmission(
                    client_request_id="prior:" + str(obs.id),
                    feedback_id=feedback.id,
                    target_skill_id=version.skill_id,
                    expected_base_version_id=version.id,
                ),
            )
            assert await worker(db, settings).run_once()
            async with db.session_factory() as session:
                row = await session.get(LearningRequestRecord, request.id)
                source = await session.scalar(
                    select(LearningSourceRecord).where(LearningSourceRecord.run_id == obs.run_id)
                )
                assert row.stage == "generate" and source is not None, row.error_code
                sources.append(source)
            await learning.cancel_request(row.id, row.lock_version)
    discovery = CandidateDiscoveryService(
        learning, store=LocalArtifactStore(settings.artifact_root), settings=settings
    )
    return db, version, settings, learning, discovery, sources


class Revised:
    def __init__(self, version):
        self.version, self.seen = version, ()

    async def generate(self, sources, *, context=None):
        self.seen = sources
        definition = deepcopy(self.version.definition)
        definition["steps"][0]["instruction"] += "; verify identifiers before accepting results"
        return SkillDefinition.model_validate(definition)


async def request_row(db):
    async with db.session_factory() as session:
        return await session.scalar(
            select(LearningRequestRecord).where(
                LearningRequestRecord.trigger == "discover",
                LearningRequestRecord.target_skill_id.is_not(None),
            )
        )


async def test_three_frozen_sources_generate_one_draft_and_retain_all_revocation_edges(
    trial_candidate,
):
    db, version, settings, _, discovery, sources = await setup(trial_candidate)
    assert await discovery.discover_revisions() == 1
    assert await discovery.discover_revisions() == 0
    request = await request_row(db)
    assert len(request.policy_snapshot["revision_aggregation"]["sources"]) == 3
    generator = Revised(version)
    for _ in range(3):
        assert await worker(db, settings, generator).run_once()
    request = await request_row(db)
    assert request.status == "ready_for_review", request.error_code
    assert len(generator.seen) == 3
    assert len(request.validation_report["aggregation_sources"]) == 3
    async with db.session_factory() as session:
        candidate = await session.get(SkillVersionRecord, request.candidate_version_id)
        links = list(
            await session.scalars(
                select(SkillSourceRecord).where(SkillSourceRecord.skill_version_id == candidate.id)
            )
        )
        assert {r.learning_source_id for r in links} == {s.id for s in sources}
        assert candidate.parent_version_id == version.id
        assert candidate.lifecycle_status.value == "draft"
        assert (await session.get(SkillRecord, version.skill_id)).active_version_id is None
        assert not request.validation_report["trial_eligible"]
    await PersonalSourceService(db.session_factory).revoke(sources[-1].id, "withdraw source")
    async with db.session_factory() as session:
        with pytest.raises(SkillAccessError):
            await SkillAccessPolicy().check(
                session, candidate.id, workspace_id=trial_candidate[-1].workspace_id
            )


@pytest.mark.parametrize("consent,independent", [(False, True), (True, False)])
async def test_aggregation_refuses_unconsented_or_identical_declared_data(
    trial_candidate,
    consent,
    independent,
):
    _, _, _, _, discovery, _ = await setup(
        trial_candidate,
        consent=consent,
        independent_files=independent,
    )
    assert await discovery.discover_revisions() == 0


@pytest.mark.parametrize("withdraw", ["source", "feedback"])
async def test_secondary_withdrawal_stops_blocked_generation_without_publishing(
    trial_candidate,
    withdraw,
):
    db, version, settings, _, discovery, sources = await setup(trial_candidate)
    assert await discovery.discover_revisions() == 1
    request = await request_row(db)
    secondary = next(s for s in sources if s.run_id != request.origin_run_id)
    started, stopped = asyncio.Event(), asyncio.Event()

    class Blocked:
        async def generate(self, sources, *, context=None):
            assert len(sources) == 3
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()

    runner = worker(db, settings, Blocked())
    assert await runner.run_once()
    running = asyncio.create_task(runner.run_once())
    try:
        await asyncio.wait_for(started.wait(), timeout=15)
        if withdraw == "source":
            await PersonalSourceService(db.session_factory).revoke(secondary.id, "withdraw")
        else:
            async with db.session_factory() as session:
                await LearningRepository(session).append_feedback(
                    secondary.run_id,
                    "withdraw-consent",
                    FeedbackPayload(
                        intent="method", verdict="incorrect", learn_from_feedback=False
                    ),
                    "local-user",
                )
                await session.commit()
        await asyncio.wait_for(stopped.wait(), timeout=2)
        assert await asyncio.wait_for(running, timeout=5)
    finally:
        running.cancel()
        await asyncio.gather(running, return_exceptions=True)
    request = await request_row(db)
    assert request.status == "failed" and request.candidate_version_id is None
    async with db.session_factory() as session:
        assert len(list(await session.scalars(select(SkillVersionRecord)))) == 1


@pytest.mark.parametrize("mutation", ["missing", "revoked", "body", "quota", "budget"])
async def test_admission_rechecks_each_source_and_discovery_limits(trial_candidate, mutation):
    db, version, settings, learning, discovery, sources = await setup(trial_candidate)
    if mutation == "missing":
        # Fresh consent supersedes the earlier frozen evidence; no retroactive borrowing.
        async with db.session_factory() as session:
            await LearningRepository(session).append_feedback(
                sources[-1].run_id,
                "new-consent",
                FeedbackPayload(
                    intent="method",
                    verdict="incorrect",
                    correction="new correction",
                    learn_from_feedback=True,
                ),
                "local-user",
            )
            await session.commit()
    elif mutation == "revoked":
        await PersonalSourceService(db.session_factory).revoke(sources[-1].id, "withdraw")
    elif mutation == "body":
        async with db.session_factory() as session:
            artifact = await session.get(ArtifactRecord, sources[-1].artifact_id)
        from pathlib import Path

        (Path(settings.artifact_root) / artifact.uri).write_text(
            "tampered sanitized evidence", encoding="utf8"
        )
    elif mutation == "budget":
        learning.generator_configuration = {"provider": "openai", "model": "no-dispatch"}
    else:
        assert await discovery.discover_revisions() == 1
    assert await discovery.discover_revisions() == 0


async def test_target_change_after_admission_blocks_generation(trial_candidate):
    db, version, settings, _, discovery, _ = await setup(trial_candidate)
    assert await discovery.discover_revisions() == 1
    async with db.session_factory() as session:
        skill = await session.get(SkillRecord, version.skill_id)
        skill.lock_version += 1
        await session.commit()
    generator = Revised(version)
    assert await worker(db, settings, generator).run_once()
    request = await request_row(db)
    assert request.status == "failed" and request.candidate_version_id is None
    assert not generator.seen


async def test_two_postgres_discovery_workers_admit_only_one_aggregation(trial_candidate):
    db, _, _, learning, discovery, _ = await setup(trial_candidate)
    if db.engine.dialect.name != "postgresql":
        pytest.skip("simultaneous row-lock admission requires PostgreSQL")
    other = CandidateDiscoveryService(learning, store=discovery.store, settings=discovery.settings)
    assert (
        sum(await asyncio.gather(discovery.discover_revisions(), other.discover_revisions())) == 1
    )
    async with db.session_factory() as session:
        requests = list(
            await session.scalars(
                select(LearningRequestRecord).where(LearningRequestRecord.trigger == "discover")
            )
        )
        assert len(requests) == 1
