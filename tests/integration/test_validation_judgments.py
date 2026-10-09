"""Human authority is durable and separate from immutable runtime metrics."""

from copy import deepcopy
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from evoagent.db.models import EvalExperimentRecord, LearningRequestRecord, ValidationJudgmentRecord
from evoagent.db.repositories.base import ConcurrentUpdateError
from evoagent.evals.coordinator import EvalCoordinator
from evoagent.evals.validators import default_validator_registry
from evoagent.learning.judgments import (
    HumanCriterionJudgment,
    HumanJudgmentSubmission,
    ValidationJudgmentService,
)
from evoagent.learning.schema import LearningError
from evoagent.skills.trials import SkillTrialService
from evoagent.tasks.lease import JobLeaseManager
from evoagent.trace.artifacts import LocalArtifactStore
from evoagent.workers.bootstrap import ConfiguredTaskHandler
from evoagent.workers.main import JobWorker
from tests.integration.test_personal_validation_execution import start

pytest_plugins = ("tests.integration.test_personal_trials",)


async def completed_report(context):
    db, settings, request, learning = await start(context)
    tasks = JobWorker(
        worker_id="human-validation-test",
        lease_manager=JobLeaseManager(db.session_factory, lease_seconds=60),
        handler=ConfiguredTaskHandler(settings, db),
        heartbeat_seconds=10,
        poll_seconds=0.01,
    )
    coordinator = EvalCoordinator(db.session_factory, default_validator_registry())
    lease = await coordinator.claim_next("human-validation-eval")
    for _ in range(8):
        assert await tasks.run_once()
        if await coordinator.run_once(lease):
            break
    else:
        pytest.fail("paired validation never completed")
    assert await learning.run_once()
    assert await learning.run_once()
    async with db.session_factory() as session:
        row = await session.get(LearningRequestRecord, request.id)
        assert row.stage == "validation_review"
    return db, settings, row


async def test_actual_human_judgments_append_replay_conflict_and_never_grant_mock_trial(
    trial_candidate,
):
    db, settings, row = await completed_report(trial_candidate)
    service = ValidationJudgmentService(
        db.session_factory,
        LocalArtifactStore(settings.artifact_root),
        learning_enabled=True,
    )
    original = deepcopy(row.validation_report)
    claims = tuple(
        HumanCriterionJudgment(
            eval_run_id=UUID(
                next(ref["id"] for ref in item["evidence_refs"] if ref["type"] == "eval_run")
            ),
            criterion_id=item["criterion_id"],
            verdict="pass",
            observed={"inspected_run_output": True},
            reason="user checked the frozen business criterion",
        )
        for item in original["items"]
    )
    payload = HumanJudgmentSubmission(
        client_request_id="human-check-1",
        expected_lock_version=row.lock_version,
        expected_report_hash=row.validation_report_hash,
        judgments=claims,
    )
    invalid = payload.model_copy(
        update={"judgments": (claims[0].model_copy(update={"eval_run_id": uuid4()}),)}
    )
    with pytest.raises(LearningError, match="validation_human_judge_binding_invalid"):
        await service.submit(row.id, invalid)
    result = await service.submit(row.id, payload)
    assert result["business_verification"] == "passed" and not result["trial_eligible"]
    assert await service.submit(row.id, payload) == result
    conflicting = payload.model_copy(
        update={"judgments": (claims[0].model_copy(update={"verdict": "fail"}),)}
    )
    with pytest.raises(ConcurrentUpdateError, match="validation_judgment_client_conflict"):
        await service.submit(row.id, conflicting)
    async with db.session_factory() as session:
        records = list(await session.scalars(select(ValidationJudgmentRecord)))
        assert len(records) == 1 and records[0].actor == "local-user"
        current = await session.get(LearningRequestRecord, row.id)
        assert current.validation_report_hash == result["report_hash"]
        assert current.validation_report_hash != row.validation_report_hash
        assert records[0].base_report_hash == row.validation_report_hash
        experiment = await session.get(EvalExperimentRecord, row.validation_experiment_id)
        assert experiment.report_hash == original["execution_report_hash"]
        assert all(item["judge_id"] == "local-user" for item in current.validation_report["items"])
    readiness = await SkillTrialService(db.session_factory).assess(row.candidate_version_id, row.id)
    assert not readiness.ready
    assert readiness.reasons == ("real_validation_and_adoption_pipeline_required",)
    async with db.session_factory() as session:
        record = await session.get(ValidationJudgmentRecord, result["id"])
        record.actor = "changed"
        with pytest.raises(ValueError, match="append-only"):
            await session.commit()
        await session.rollback()
    # A stale report cannot receive a new client operation.
    stale = payload.model_copy(update={"client_request_id": "human-check-stale"})
    with pytest.raises(ConcurrentUpdateError, match="validation_judgment_report_conflict"):
        await service.submit(row.id, stale)
    async with db.session_factory() as session:
        current = await session.get(LearningRequestRecord, row.id)
    correction = HumanJudgmentSubmission(
        client_request_id="human-check-correction",
        expected_lock_version=current.lock_version,
        expected_report_hash=current.validation_report_hash,
        judgments=(
            claims[0].model_copy(
                update={"verdict": "fail", "reason": "user found an unmet criterion"}
            ),
        ),
    )
    corrected = await service.submit(row.id, correction)
    assert corrected["business_verification"] == "failed" and not corrected["trial_eligible"]
    assert await service.submit(row.id, payload) == result
    async with db.session_factory() as session:
        records = list(
            await session.scalars(
                select(ValidationJudgmentRecord).order_by(ValidationJudgmentRecord.revision)
            )
        )
        assert len(records) == 2
        assert records[1].base_report_hash == records[0].resulting_report_hash
        assert records[0].resulting_report["business_verification"] == "passed"
        from evoagent.db.models import LearningPolicyRecord

        policy = await session.get(LearningPolicyRecord, row.workspace_id)
        policy.mode = "off"
        await session.commit()
    with pytest.raises(LearningError, match="learning_policy_off"):
        await service.submit(
            row.id, correction.model_copy(update={"client_request_id": "off-check"})
        )
