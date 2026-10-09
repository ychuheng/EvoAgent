"""Actual offline Task execution; unknown business verdicts stay unknown."""

from uuid import UUID

import pytest
from sqlalchemy import select

from evoagent.db.models import (
    EvalExperimentRecord,
    EvalRunRecord,
    LearningPolicyRecord,
    LearningRequestRecord,
    LearningSourceRecord,
    RunRecord,
    SkillVersionRecord,
)
from evoagent.evals.coordinator import EvalCoordinator
from evoagent.evals.validators import default_validator_registry
from evoagent.learning.validation_guard import PersonalValidationRunGuard
from evoagent.memory.maintenance import MaintenanceWorker
from evoagent.memory.schema import MemoryError
from evoagent.skills.trials import SkillTrialService
from evoagent.tasks.lease import JobLeaseManager
from evoagent.trace.artifacts import LocalArtifactStore
from evoagent.workers.bootstrap import ConfiguredTaskHandler
from evoagent.workers.main import JobWorker
from tests.integration.test_learning_worker import worker
from tests.integration.test_validation_admission import inputs

pytest_plugins = ("tests.integration.test_personal_trials",)


async def start(context):
    service, parent, payload, _ = await inputs(context)
    prepared = await service.prepare_cases(parent.id, payload)
    _, db, _, _, _, _ = context
    settings = context[0]._transport.app.state.settings
    existing = worker(db, settings)
    handler = existing.handlers["learning_validate"]
    learning = MaintenanceWorker(
        db.session_factory,
        LocalArtifactStore(settings.artifact_root),
        handlers={"learning_validate": handler, "learning_validation_completed": handler},
        allowed_kinds={"learning_validate", "learning_validation_completed"},
    )
    await service.start(prepared.id, prepared.lock_version)
    assert await learning.run_once()
    async with db.session_factory() as session:
        request = await session.get(LearningRequestRecord, prepared.id)
        assert request.stage == "waiting_validation", request.error_code
    return db, settings, request, learning


async def test_actual_task_to_completed_experiment_to_pending_user_report(trial_candidate):
    db, settings, request, learning = await start(trial_candidate)
    tasks = JobWorker(
        worker_id="validation-test",
        lease_manager=JobLeaseManager(db.session_factory, lease_seconds=60),
        handler=ConfiguredTaskHandler(settings, db),
        heartbeat_seconds=10,
        poll_seconds=0.01,
    )
    coordinator = EvalCoordinator(db.session_factory, default_validator_registry())
    lease = await coordinator.claim_next("validation-eval")
    assert lease is not None
    completed = False
    for _ in range(8):
        assert await tasks.run_once()
        completed = await coordinator.run_once(lease)
        if completed:
            break
    assert completed
    # The final experiment transaction stores an outbox, not a volatile callback.
    assert await learning.run_once()
    assert await learning.run_once()
    async with db.session_factory() as session:
        row = await session.get(LearningRequestRecord, request.id)
        assert row.stage == "validation_review" and row.status == "ready_for_review", row.error_code
        report = row.validation_report
        assert report["business_verification"] == "pending" and not report["trial_eligible"]
        assert {item["verdict"] for item in report["items"]} == {"unknown"}
        assert all(
            item["judge_origin"] == "user" and item["judge_id"] is None for item in report["items"]
        )
        experiment = await session.get(EvalExperimentRecord, row.validation_experiment_id)
        assert report["execution_report_hash"] == experiment.report_hash
        assert row.validation_report_hash != experiment.report_hash
        candidate = await session.get(SkillVersionRecord, row.candidate_version_id)
        assert (
            candidate.evaluation_report_hash is None and candidate.lifecycle_status.value == "draft"
        )
        runs = list(
            await session.scalars(
                select(EvalRunRecord).where(EvalRunRecord.experiment_id == experiment.id)
            )
        )
        assert len(runs) == 4
        for evaluation in runs:
            run = await session.get(RunRecord, evaluation.run_id)
            assert (
                run.status.value == "completed"
                and run.config_hash
                and run.started_at
                and run.ended_at
            )
        assert all(item["evidence_refs"] for item in report["items"])
    readiness = await SkillTrialService(db.session_factory).assess(candidate.id, request.id)
    assert not readiness.ready


async def test_internal_run_guard_refuses_cancellation_before_another_boundary(trial_candidate):
    db, settings, request, _ = await start(trial_candidate)
    async with db.session_factory() as session:
        evaluation = await session.scalar(
            select(EvalRunRecord).where(
                EvalRunRecord.experiment_id == request.validation_experiment_id
            )
        )
    guard = await PersonalValidationRunGuard.for_run(
        db.session_factory,
        LocalArtifactStore(settings.artifact_root),
        evaluation.run_id,
        learning_enabled=True,
    )
    assert guard is not None
    await guard.check()
    from evoagent.learning.service import LearningService

    await LearningService(db.session_factory).cancel_request(request.id, request.lock_version)
    with pytest.raises(MemoryError, match="personal_validation_authorization_revoked"):
        await guard.check()


@pytest.mark.parametrize("mutation", ["policy", "source", "candidate", "input"])
async def test_internal_guard_rechecks_mutable_authority(trial_candidate, mutation):
    db, settings, request, _ = await start(trial_candidate)
    async with db.session_factory() as session:
        evaluation = await session.scalar(
            select(EvalRunRecord).where(
                EvalRunRecord.experiment_id == request.validation_experiment_id
            )
        )
    guard = await PersonalValidationRunGuard.for_run(
        db.session_factory,
        LocalArtifactStore(settings.artifact_root),
        evaluation.run_id,
        learning_enabled=True,
    )
    await guard.check()
    async with db.session_factory() as session:
        if mutation == "policy":
            row = await session.get(LearningPolicyRecord, request.workspace_id)
            row.mode = "off"
        elif mutation == "source":
            row = await session.get(LearningSourceRecord, UUID(request.frozen_inputs["source_id"]))
            row.revocation_epoch += 1
            row.status = "revoked"
        elif mutation == "candidate":
            row = await session.get(SkillVersionRecord, request.candidate_version_id)
            from evoagent.skills.lifecycle import SkillVersionStatus

            row.lifecycle_status = SkillVersionStatus.REJECTED
        else:
            from evoagent.db.models import TaskRecord

            row = await session.get(TaskRecord, evaluation.task_id)
            row.goal = "changed task input"
        await session.commit()
    with pytest.raises(MemoryError):
        await guard.check()
