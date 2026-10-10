"""Personal experiment identity and arm contracts, without paid model calls."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy import select

from evoagent.db.models import (
    EvalDatasetRecord,
    EvalExperimentRecord,
    EvalRunRecord,
    LearningRequestRecord,
    MaintenanceJobRecord,
    RunRecord,
    SkillSourceRecord,
    SkillVersionRecord,
    TaskRecord,
)
from evoagent.evals.coordinator import EvalCoordinator, EvalCoordinatorError
from evoagent.evals.datasets import EvalDatasetService
from evoagent.evals.lifecycle import EvalExperimentStatus
from evoagent.evals.metrics import EvaluationReportService
from evoagent.evals.schema import EvalCaseDefinition, EvalDatasetDefinition, ValidatorSpec
from evoagent.evals.validators import default_validator_registry
from evoagent.learning.jobs import LearningJobGuard
from evoagent.learning.repository import LearningRepository
from evoagent.skills.canonical import content_hash
from evoagent.skills.lifecycle import SkillVersionStatus
from evoagent.skills.trials import TrialScope
from evoagent.trace.artifacts import ArtifactService, LocalArtifactStore

pytest_plugins = ("tests.integration.test_personal_trials",)


async def prepare(context, *, comparison=False, split="train", task_family="data"):
    _, db, run_id, state, candidate, skill = context
    service = EvalDatasetService(db.session_factory)
    dataset = await service.import_definition(
        EvalDatasetDefinition(
            purpose="personal_dev",
            name="personal_cases",
            version=1,
            cases=tuple(
                EvalCaseDefinition(
                    case_key="case_" + str(index),
                    task_family=task_family,
                    split=split,
                    public_input={
                        "goal": "Preserve identifiers",
                        "inputs": {"rows": [str(index) + "001"]},
                    },
                    private_validators=(ValidatorSpec(name="run_completed"),),
                )
                for index in range(2)
            ),
        )
    )
    await service.freeze(dataset.id)
    control_id = None
    async with db.session_factory() as session:
        parent = await session.get(LearningRequestRecord, UUID(state["id"]))
        if comparison:
            # A source-linked historical sibling is enough to test two pinned
            # arms; this is not a benefit comparison or actual validation run.
            control = SkillVersionRecord(
                skill_id=skill.id,
                version=2,
                schema_version=2,
                definition=candidate.definition,
                content_hash=candidate.content_hash,
                extraction_key=content_hash({"control": True}),
                lifecycle_status=SkillVersionStatus.DRAFT,
            )
            session.add(control)
            await session.flush()
            link = await session.scalar(
                select(SkillSourceRecord).where(SkillSourceRecord.skill_version_id == candidate.id)
            )
            session.add(
                SkillSourceRecord(
                    skill_version_id=control.id,
                    source_run_id=link.source_run_id,
                    source_kind="personal",
                    learning_source_id=link.learning_source_id,
                    trace_artifact_id=link.trace_artifact_id,
                    source_trace_hash=link.source_trace_hash,
                )
            )
            control_id = control.id
        policy = {**parent.policy_snapshot, "validation_mode": "personal_validation"}
        frozen = {
            **parent.frozen_inputs,
            "parent_request_id": str(parent.id),
            "candidate_version_id": str(candidate.id),
            "candidate_content_hash": candidate.content_hash,
            "validation_input_manifest_hash": dataset.content_hash,
            "validation_criteria_hash": content_hash({"criterion": "run_completed"}),
            "validation_policy_hash": content_hash(policy),
            "validator_version": "1",
            "target_scope_key": TrialScope(skill.workspace_id).key,
        }
        request = await LearningRepository(session).append_request(
            workspace_id=skill.workspace_id,
            origin_run_id=run_id,
            client_request_id="validate-child",
            kind="validate",
            frozen_inputs=frozen,
            policy_snapshot=policy,
            request_body={"dataset_id": str(dataset.id)},
            parent_request_id=parent.id,
            target_skill_id=skill.id,
            base_version_id=control_id,
        )
        request.candidate_version_id, request.stage = candidate.id, "task_validate"
        job = MaintenanceJobRecord(
            dedupe_key="validation-stage",
            kind="learning_validate",
            learning_request_id=request.id,
            status="running",
            lease_owner="validation-owner",
            lease_epoch=1,
            lease_expires_at=datetime.now(UTC) + timedelta(seconds=120),
            payload={"request_lock_version": request.lock_version},
        )
        session.add(job)
        await session.commit()
    return dataset, request, control_id, LearningJobGuard(job.id, "validation-owner", 1)


@pytest.mark.parametrize("comparison", [False, True])
async def test_personal_pairs_have_explicit_arms_actual_modes_and_dev_roles(
    trial_candidate, comparison
):
    _, db, _, _, candidate, _ = trial_candidate
    dataset, request, control_id, guard = await prepare(trial_candidate, comparison=comparison)
    coordinator = EvalCoordinator(db.session_factory, default_validator_registry())
    params = dict(
        learning_request_id=request.id,
        dataset_id=dataset.id,
        candidate_version_id=candidate.id,
        comparison_version_id=control_id,
        provider="mock",
        model="mock",
        repeats=1,
        code_version="test",
        job_guard=guard,
    )
    experiment = await coordinator.create_personal_validation(**params)
    assert (await coordinator.create_personal_validation(**params)).id == experiment.id
    async with db.session_factory() as session:
        rows = list(
            await session.scalars(
                select(EvalRunRecord).where(EvalRunRecord.experiment_id == experiment.id)
            )
        )
        assert len(rows) == 4
        for row in rows:
            run = await session.get(RunRecord, row.run_id)
            assert run.data_role == "dev"
            assert (await session.get(TaskRecord, run.task_id)).family == "data"
            if row.arm == "control":
                assert row.mode.value == ("pinned_skill" if comparison else "baseline")
                assert row.skill_version_id == control_id
            else:
                assert row.mode.value == "pinned_skill" and row.skill_version_id == candidate.id
            assert row.paired_eval_run_id is not None
        assert (
            await session.get(SkillVersionRecord, candidate.id)
        ).lifecycle_status is SkillVersionStatus.DRAFT
        assert experiment.gate_report_hash is None


async def test_personal_coordinator_rejects_holdout_and_paid_dispatch_before_side_effects(
    trial_candidate,
):
    _, db, _, _, candidate, _ = trial_candidate
    dataset, request, _, guard = await prepare(trial_candidate, split="holdout")
    coordinator = EvalCoordinator(db.session_factory, default_validator_registry())
    params = dict(
        learning_request_id=request.id,
        dataset_id=dataset.id,
        candidate_version_id=candidate.id,
        provider="mock",
        model="mock",
        repeats=1,
        code_version="test",
        job_guard=guard,
    )
    with pytest.raises(EvalCoordinatorError, match="rejects_holdout"):
        await coordinator.create_personal_validation(**params)
    with pytest.raises(EvalCoordinatorError, match="paid_dispatch_not_connected"):
        await coordinator.create_personal_validation(**{**params, "provider": "openai"})
    async with db.session_factory() as session:
        assert not list(await session.scalars(select(EvalRunRecord)))


async def test_two_pinned_arm_report_cannot_change_formal_skill_evidence(trial_candidate, tmp_path):
    _, db, _, _, candidate, _ = trial_candidate
    dataset, request, control_id, guard = await prepare(trial_candidate, comparison=True)
    experiment = await EvalCoordinator(
        db.session_factory, default_validator_registry()
    ).create_personal_validation(
        learning_request_id=request.id,
        dataset_id=dataset.id,
        candidate_version_id=candidate.id,
        comparison_version_id=control_id,
        provider="mock",
        model="mock",
        repeats=1,
        code_version="test",
        job_guard=guard,
    )
    reports = EvaluationReportService(db.session_factory)
    artifacts = ArtifactService(LocalArtifactStore(tmp_path / "reports"), db.session_factory)
    with pytest.raises(ValueError, match="requires completed"):
        await reports.freeze(experiment.id, artifacts)
    async with db.session_factory() as session:
        version = await session.get(SkillVersionRecord, candidate.id)
        version.evaluation_report_hash = content_hash({"formal_evidence": True})
        formal_hash = version.evaluation_report_hash
        # Only report serialization/persistence is tested here; seeded terminal
        # rows are not evidence of task execution or trial eligibility.
        record = await session.get(EvalExperimentRecord, experiment.id)
        record.status = EvalExperimentStatus.COMPLETED
        for row in await session.scalars(select(EvalRunRecord)):
            row.metrics = {**row.metrics, "state": "completed"}
        await session.commit()
    report, digest, _ = await reports.freeze(experiment.id, artifacts)
    assert report.purpose == "personal_validation" and report.schema_version == 2
    assert all(pair.control_mode == "pinned_skill" for pair in report.pairs)
    assert all(pair.control_version_id == control_id for pair in report.pairs)
    assert all(pair.treatment_version_id == candidate.id for pair in report.pairs)
    assert (await reports.freeze(experiment.id, artifacts))[1] == digest
    async with db.session_factory() as session:
        version = await session.get(SkillVersionRecord, candidate.id)
        assert version.evaluation_report_hash == formal_hash
        assert version.gate_report_hash is None


@pytest.mark.parametrize("entity", ["dataset", "experiment"])
async def test_purpose_cannot_be_changed_to_launder_personal_validation(trial_candidate, entity):
    _, db, _, _, candidate, _ = trial_candidate
    dataset, request, _, guard = await prepare(trial_candidate)
    experiment = await EvalCoordinator(
        db.session_factory, default_validator_registry()
    ).create_personal_validation(
        learning_request_id=request.id,
        dataset_id=dataset.id,
        candidate_version_id=candidate.id,
        provider="mock",
        model="mock",
        repeats=1,
        code_version="test",
        job_guard=guard,
    )
    async with db.session_factory() as session:
        row = await session.get(
            EvalDatasetRecord if entity == "dataset" else EvalExperimentRecord,
            dataset.id if entity == "dataset" else experiment.id,
        )
        row.purpose = "formal"
        with pytest.raises(ValueError, match="immutable"):
            await session.flush()
        await session.rollback()


async def test_invalid_personal_case_family_cannot_create_tasks(trial_candidate):
    _, db, _, _, candidate, _ = trial_candidate
    dataset, request, _, guard = await prepare(trial_candidate, task_family="guessed-from-goal")
    coordinator = EvalCoordinator(db.session_factory, default_validator_registry())
    with pytest.raises(EvalCoordinatorError, match="personal_validation_task_family_invalid"):
        await coordinator.create_personal_validation(
            learning_request_id=request.id,
            dataset_id=dataset.id,
            candidate_version_id=candidate.id,
            provider="mock",
            model="mock",
            repeats=1,
            code_version="test",
            job_guard=guard,
        )
    async with db.session_factory() as session:
        assert not list(await session.scalars(select(EvalRunRecord)))
