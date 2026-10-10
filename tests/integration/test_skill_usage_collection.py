"""Synthetic durable bindings test observation contracts, not real adoption."""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import select

from evoagent.db.models import (
    ArtifactRecord,
    RunRecord,
    RunSkillSelectionRecord,
    SkillObservationRecord,
    TaskRecord,
)
from evoagent.learning.repository import LearningRepository
from evoagent.learning.schema import FeedbackPayload
from evoagent.privacy.redaction import POLICY_VERSION
from evoagent.runtime.run_config import RunConfigSnapshot, sha256_text
from evoagent.skills.canonical import content_hash
from evoagent.skills.selection_snapshot import (
    SkillSelectionScope,
    SkillSelectionSnapshot,
    selection_hash,
)
from evoagent.skills.usage import SkillUsageService
from tests.integration.test_personal_trials import seed_binding

pytest_plugins = ("tests.integration.test_personal_trials",)


async def setup_use(context):
    trial = await seed_binding(context)
    _, db, run_id, _, version, skill = context
    scope = SkillSelectionScope(workspace_id=skill.workspace_id)
    selection = SkillSelectionSnapshot(
        version_id=version.id,
        content_hash=version.content_hash,
        rendered_hash=sha256_text("synthetic frozen view"),
        origin="trial",
        scope=scope,
        trial_id=trial.id,
    )
    body = json.loads((Path(__file__).parents[1] / "fixtures/run_config/pre_v3.json").read_text())[
        "2"
    ]["body"]
    body.update(
        schema_version=3,
        run_mode="retrieval",
        selector_version="skill-selector-v1",
        renderer_version=2,
        skill_renderer_version=2,
        selected_skills=[selection.model_dump(mode="json")],
        under_test_skill_version_id=None,
        skill_selection_hash=selection_hash(
            selector_version="skill-selector-v1", renderer_version=2, selections=(selection,)
        ),
        skill_retrieval_top_k=1,
        skill_version_id=str(version.id),
        skill_content_hash=version.content_hash,
        skill_context_hash=selection.rendered_hash,
    )
    config = RunConfigSnapshot.model_validate(body)
    async with db.session_factory() as session:
        original = await session.get(RunRecord, run_id)
        original_task = await session.get(TaskRecord, original.task_id)
        task = TaskRecord(
            session_id=original_task.session_id,
            goal="preserve identifiers",
            family="data",
            status="completed",
            frozen_inputs={
                "files": [
                    {
                        "path": "input.csv",
                        "sha256": "a" * 64,
                        "size_bytes": 5,
                        "kind": "fixture",
                    }
                ]
            },
        )
        session.add(task)
        await session.flush()
        run = RunRecord(
            task_id=task.id,
            provider="mock",
            model="mock",
            status="completed",
            data_role="personal",
            run_mode="retrieval",
            ended_at=datetime.now(UTC),
            config_snapshot=config.model_dump(mode="json"),
            config_hash=config.content_hash(),
        )
        session.add(run)
        await session.flush()
        session.add(
            RunSkillSelectionRecord(
                run_id=run.id,
                skill_version_id=version.id,
                trial_id=trial.id,
                origin="trial",
                mode="retrieval",
                rank=1,
                score=1,
                query_terms=[],
                content_hash=version.content_hash,
                rendered_hash=selection.rendered_hash,
                scope_key=content_hash(scope.model_dump(mode="json")),
                applicability={"status": "applicable"},
                selection_policy_version="skill-selector-v1",
            )
        )
        artifact = ArtifactRecord(
            run_id=run.id,
            type="test_output",
            uri="synthetic:test",
            content_hash=sha256_text("result"),
            size_bytes=6,
            redaction_status="verified",
            redaction_policy_version=POLICY_VERSION,
            redaction_checked_hash=sha256_text("result"),
        )
        session.add(artifact)
        await session.commit()
    return db, run, version, artifact


@pytest.mark.parametrize(
    "mutation", [None, "wrong_version", "wrong_step", "wrong_artifact", "model_actor", "old_policy"]
)
async def test_explicit_user_proof_is_bound_and_other_claims_remain_unknown(
    trial_candidate, mutation
):
    from uuid import uuid4

    db, run, version, artifact = await setup_use(trial_candidate)
    claim = {
        "type": "skill_observation",
        "schema_version": 1,
        "version_id": str(version.id),
        "criterion_id": "identifiers_preserved",
        "outcome": "verified_failure",
        "attribution": "skill_related",
        "associated_steps": [version.definition["steps"][0]["id"]],
        "artifacts": [{"artifact_id": str(artifact.id), "content_hash": artifact.content_hash}],
    }
    if mutation == "wrong_version":
        claim["version_id"] = str(uuid4())
    elif mutation == "wrong_step":
        claim["associated_steps"] = ["invented_step"]
    elif mutation == "wrong_artifact":
        claim["artifacts"][0]["artifact_id"] = str(uuid4())
    async with db.session_factory() as session:
        if mutation == "old_policy":
            old = await session.get(ArtifactRecord, artifact.id)
            old.redaction_policy_version = POLICY_VERSION - 1
        feedback = await LearningRepository(session).append_feedback(
            run.id,
            "user-proof",
            FeedbackPayload(
                intent="method",
                verdict="incorrect",
                evidence_refs=[claim],
            ),
            "model" if mutation == "model_actor" else "local-user",
        )
        await session.commit()
    service = SkillUsageService(db.session_factory)
    first = await service.collect(run.id, feedback.revision)
    assert await service.collect(run.id, feedback.revision) == first
    async with db.session_factory() as session:
        rows = list(await session.scalars(select(SkillObservationRecord)))
        assert len(rows) == 1
        assert rows[0].outcome == ("verified_failure" if mutation is None else "unknown")


async def test_outbox_runs_with_learning_disabled_and_replay_is_idempotent(trial_candidate):
    from evoagent.learning.service import LearningService
    from evoagent.memory.maintenance import MaintenanceWorker
    from evoagent.skills.observation_jobs import ObservationJobHandler

    db, run, _, _ = await setup_use(trial_candidate)
    service = LearningService(db.session_factory, learning_enabled=False)
    feedback = await service.record_feedback(
        run.id, FeedbackPayload(intent="method", verdict="helpful"), client_request_id="generic"
    )
    await service.record_feedback(
        run.id, FeedbackPayload(intent="method", verdict="helpful"), client_request_id="generic"
    )
    worker = MaintenanceWorker(
        db.session_factory,
        None,
        lane="critical",
        handlers={"learning_observe": ObservationJobHandler(db.session_factory)},
        allowed_kinds={"learning_observe"},
    )
    assert await worker.run_once()
    assert not await worker.run_once()
    async with db.session_factory() as session:
        rows = list(await session.scalars(select(SkillObservationRecord)))
        assert len(rows) == 1 and rows[0].feedback_revision == feedback.revision
        assert rows[0].outcome == "unknown"


async def test_stale_maintenance_epoch_cannot_append_observation(trial_candidate):
    from evoagent.learning.schema import LearningError
    from evoagent.memory.maintenance import MaintenanceWorker
    from evoagent.skills.observation_jobs import (
        ObservationJobGuard,
        ObservationJobHandler,
        schedule_observation,
    )

    db, run, _, _ = await setup_use(trial_candidate)
    async with db.session_factory() as session:
        current = await session.get(RunRecord, run.id)
        await schedule_observation(session, current)
        await session.commit()
    worker = MaintenanceWorker(
        db.session_factory,
        None,
        lane="critical",
        handlers={"learning_observe": ObservationJobHandler(db.session_factory)},
    )
    job_id, epoch = await worker.claim()
    guard = ObservationJobGuard(job_id, worker.owner, epoch - 1, run.id, 0)
    with pytest.raises(LearningError, match="observation_job_fenced"):
        await SkillUsageService(db.session_factory).collect(run.id, job_guard=guard)
    async with db.session_factory() as session:
        assert not list(await session.scalars(select(SkillObservationRecord)))
    await worker.execute(job_id, epoch)
    async with db.session_factory() as session:
        assert len(list(await session.scalars(select(SkillObservationRecord)))) == 1


async def test_terminal_transaction_enqueues_observation(trial_candidate):
    from datetime import timedelta

    from evoagent.db.models import MaintenanceJobRecord
    from evoagent.tasks.lease import JobLease, JobLeaseManager, TaskExecutionResult
    from evoagent.tasks.state_machine import PersistentRunStatus

    db, run, _, _ = await setup_use(trial_candidate)
    expiry = datetime.now(UTC) + timedelta(minutes=2)
    async with db.session_factory() as session:
        current = await session.get(RunRecord, run.id)
        task = await session.get(TaskRecord, current.task_id)
        task.status = "running"
        task.lease_owner = "terminal-test"
        task.lease_epoch = 1
        task.lease_expires_at = expiry
        current.status = "running"
        current.ended_at = None
        await session.commit()
    lease = JobLease(run.task_id, run.id, "terminal-test", expiry, 1, 1)
    await JobLeaseManager(db.session_factory, lease_seconds=60).finalize(
        lease, TaskExecutionResult(status=PersistentRunStatus.COMPLETED, final_answer="done")
    )
    async with db.session_factory() as session:
        job = await session.scalar(
            select(MaintenanceJobRecord).where(
                MaintenanceJobRecord.dedupe_key == f"observe:v1:{run.id}:0"
            )
        )
        assert job is not None and job.status == "pending"


async def test_pending_projection_blocks_health_until_bounded_repair(trial_candidate):
    from evoagent.memory.maintenance import MaintenanceWorker
    from evoagent.skills.observation_jobs import ObservationJobHandler

    db, run, _, _ = await setup_use(trial_candidate)
    service = SkillUsageService(db.session_factory)
    async with db.session_factory() as session:
        binding = await session.scalar(select(RunSkillSelectionRecord))
        trial_id = binding.trial_id
    assert (await service.evaluate_trial_health(trial_id)).status == "health_pending"
    rows, cursor = await service.scan_pending_observations(limit=1)
    assert rows == (run.id,) and cursor == run.id
    worker = MaintenanceWorker(
        db.session_factory,
        None,
        lane="critical",
        handlers={"learning_observe": ObservationJobHandler(db.session_factory)},
    )
    assert await worker.run_once()
    assert (await service.evaluate_trial_health(trial_id)).status == "healthy"
    assert await service.scan_pending_observations() == ((), None)
