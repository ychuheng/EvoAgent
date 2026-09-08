from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

from evoagent.core.models import ToolRisk
from evoagent.db.base import Base
from evoagent.db.models import (
    EvalExperimentRecord,
    EvalRunRecord,
    RunRecord,
    SkillRecord,
    SkillVersionRecord,
    TaskRecord,
    TurnRecord,
)
from evoagent.db.session import Database
from evoagent.evals.coordinator import EvalCoordinator
from evoagent.evals.datasets import EvalDatasetService
from evoagent.evals.gates import GateReport, QualityGate, SkillEvaluationService
from evoagent.evals.lifecycle import EvalExperimentKind, EvalExperimentStatus, EvalRunMode
from evoagent.evals.metrics import (
    EvaluationReport,
    EvaluationReportService,
    MetricsCollector,
    PairMetrics,
    RunMetrics,
    TaskFamilyMetrics,
)
from evoagent.evals.schema import EvalCaseDefinition, EvalDatasetDefinition, ValidatorSpec
from evoagent.evals.validators import default_validator_registry
from evoagent.runtime.run_config import RunConfigSnapshot, RunMode, sha256_text
from evoagent.skills.canonical import content_hash
from evoagent.skills.lifecycle import SkillVersionStatus
from evoagent.skills.schema import SkillDefinition, SkillPreconditions, ToolStep
from evoagent.skills.service import SkillService, SkillVersionConflictError
from evoagent.skills.validation import SkillDefinitionValidator
from evoagent.tasks.state_machine import PersistentRunStatus, TaskStatus
from evoagent.tools.builtin.calculator import CalculatorTool
from evoagent.tools.registry import ToolRegistry
from evoagent.trace.artifacts import ArtifactService, LocalArtifactStore


def definition() -> SkillDefinition:
    return SkillDefinition(
        name="math_report",
        description="生成数学报告",
        triggers=("数学", "报告"),
        preconditions=SkillPreconditions(
            allowed_tools=("calculator",), max_effective_risk=ToolRisk.R0
        ),
        steps=(ToolStep(id="calculate", tool="calculator", args={"expression": "1+1"}),),
        success_criteria=("给出结果",),
        validators=("run_completed",),
    )


async def prepare(database: Database):
    datasets = EvalDatasetService(database.session_factory)
    dataset = await datasets.import_definition(
        EvalDatasetDefinition(
            name="comparison",
            version=1,
            cases=(
                EvalCaseDefinition(
                    case_key="hidden_math",
                    task_family="math",
                    split="holdout",
                    public_input={"goal": "生成数学报告"},
                    private_validators=(ValidatorSpec(name="run_completed"),),
                ),
            ),
        )
    )
    await datasets.freeze(dataset.id)
    skill_definition = definition()
    async with database.session_factory() as session:
        skill = SkillRecord(
            name=skill_definition.name,
            slug=skill_definition.name,
            description=skill_definition.description,
        )
        session.add(skill)
        await session.flush()
        version = SkillVersionRecord(
            skill_id=skill.id,
            version=1,
            schema_version=1,
            definition=skill_definition.model_dump(mode="json"),
            content_hash=content_hash(skill_definition.model_dump(mode="json")),
            extraction_key=sha256_text("evaluation-version"),
            lifecycle_status=SkillVersionStatus.DRAFT,
        )
        session.add(version)
        await session.commit()
        return dataset, skill, version


def run_snapshot(mode: EvalRunMode, version: SkillVersionRecord) -> RunConfigSnapshot:
    common = {
        "provider": "mock",
        "model": "mock-model",
        "system_prompt_hash": sha256_text("system"),
        "tool_manifest_hash": sha256_text("tools"),
        "policy_hash": sha256_text("policy"),
        "max_iterations": 8,
        "max_total_tokens": 1_000,
        "model_timeout_seconds": 10,
        "task_timeout_seconds": 20,
        "tool_timeout_seconds": 5,
        "code_version": "test",
    }
    if mode is EvalRunMode.BASELINE:
        return RunConfigSnapshot(**common, run_mode=RunMode.BASELINE)
    return RunConfigSnapshot(
        **common,
        run_mode=RunMode.PINNED_SKILL,
        skill_version_id=version.id,
        skill_content_hash=version.content_hash,
        skill_context_hash=sha256_text("rendered-skill"),
    )


async def complete_position(
    database: Database, experiment_id, version: SkillVersionRecord, position: int
) -> None:
    now = datetime.now(UTC)
    async with database.session_factory() as session:
        eval_runs = tuple(
            await session.scalars(
                select(EvalRunRecord).where(EvalRunRecord.experiment_id == experiment_id)
            )
        )
        for item in eval_runs:
            if item.metrics["position"] != position:
                continue
            run = await session.get(RunRecord, item.run_id)
            task = await session.get(TaskRecord, item.task_id)
            assert run is not None and task is not None
            snapshot = run_snapshot(item.mode, version)
            run.status = PersistentRunStatus.COMPLETED
            run.final_answer = "# 总结\n结果为 2。"
            run.started_at = now
            run.ended_at = now + timedelta(milliseconds=20 + position)
            run.config_snapshot = snapshot.model_dump(mode="json")
            run.config_hash = snapshot.content_hash()
            task.status = TaskStatus.COMPLETED
            session.add(
                TurnRecord(
                    run_id=run.id,
                    sequence=1,
                    status="completed",
                    usage={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
                )
            )
        await session.commit()


@pytest.mark.asyncio
async def test_recoverable_pair_report_gate_and_publish(tmp_path: Path) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'eval.db'}")
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    dataset, skill, version = await prepare(database)
    validators = default_validator_registry()
    coordinator = EvalCoordinator(database.session_factory, validators, lease_seconds=10)
    experiment = await coordinator.create_experiment(
        dataset_id=dataset.id,
        skill_version_id=version.id,
        provider="mock",
        model="mock-model",
        repeats=2,
        code_version="test",
    )
    async with database.session_factory() as session:
        rows = tuple(
            await session.scalars(
                select(EvalRunRecord).where(EvalRunRecord.experiment_id == experiment.id)
            )
        )
        assert len(rows) == 4
        assert sum(item.metrics["position"] == 1 for item in rows) == 2
        assert {item.mode for item in rows} == {EvalRunMode.BASELINE, EvalRunMode.PINNED_SKILL}

    now = datetime.now(UTC)
    lease = await coordinator.claim_next("eval-worker", now=now)
    assert lease is not None
    assert await coordinator.claim_next("other", now=now) is None
    await complete_position(database, experiment.id, version, 1)
    assert await coordinator.run_once(lease, now=now + timedelta(seconds=1)) is False

    await complete_position(database, experiment.id, version, 2)
    assert await coordinator.run_once(lease, now=now + timedelta(seconds=2)) is True
    async with database.session_factory() as session:
        stored = await session.get(EvalExperimentRecord, experiment.id)
        assert stored is not None and stored.status is EvalExperimentStatus.COMPLETED
        eval_runs = tuple(
            await session.scalars(
                select(EvalRunRecord).where(EvalRunRecord.experiment_id == experiment.id)
            )
        )
        assert all(item.passed and item.comparable for item in eval_runs)

    registry = ToolRegistry((CalculatorTool(),))
    definition_validator = SkillDefinitionValidator(
        registry, allowed_tools=frozenset({"calculator"})
    )
    reports = EvaluationReportService(database.session_factory)
    report = await reports.build(experiment.id)
    assert report.pair_count == 2
    assert report.comparable_pairs == 2
    assert report.efficiency_comparable_pairs == 2

    artifacts = ArtifactService(
        LocalArtifactStore(tmp_path / "artifacts"), database.session_factory
    )
    gate = QualityGate(
        database.session_factory,
        definition_validator,
        validators,
        artifacts,
        minimum_sources=0,
    )
    evaluation_service = SkillEvaluationService(
        database.session_factory, coordinator, reports, gate, artifacts
    )
    gate_report, gate_hash = await evaluation_service.finalize(experiment.id)
    assert gate_report.passed is True
    assert gate_hash == gate_report.report_hash()
    repeated_gate, repeated_hash = await evaluation_service.finalize(experiment.id)
    assert repeated_gate == gate_report
    assert repeated_hash == gate_hash
    async with database.session_factory() as session:
        immutable_experiment = await session.get(EvalExperimentRecord, experiment.id)
        assert immutable_experiment is not None
        immutable_experiment.gate_report = {
            **immutable_experiment.gate_report,
            "passed": False,
        }
        with pytest.raises(ValueError, match="cannot be replaced"):
            await session.commit()
        await session.rollback()
    async with database.session_factory() as session:
        current = await session.get(SkillVersionRecord, version.id)
        assert current is not None
        assert current.lifecycle_status is SkillVersionStatus.REVIEW_REQUIRED

    service = SkillService(database.session_factory, definition_validator)
    published = await service.approve(
        version_id=version.id,
        expected_lock_version=skill.lock_version,
        reviewer="local-reviewer",
        reason="确定性门禁已经通过",
    )
    assert published.active_version_id == version.id

    # 发布第二版后，旧版必须变为 RETIRED，随后才能按兼容性检查原子回滚。
    async with database.session_factory() as session:
        second_version = SkillVersionRecord(
            skill_id=skill.id,
            parent_version_id=version.id,
            version=2,
            schema_version=1,
            definition=definition().model_dump(mode="json"),
            content_hash=content_hash(definition().model_dump(mode="json")),
            extraction_key=sha256_text("second-version"),
            lifecycle_status=SkillVersionStatus.REVIEW_REQUIRED,
        )
        session.add(second_version)
        await session.flush()
        second_experiment = EvalExperimentRecord(
            kind=EvalExperimentKind.SKILL_COMPARISON,
            skill_version_id=second_version.id,
            dataset_id=dataset.id,
            status=EvalExperimentStatus.COMPLETED,
            config_snapshot={"test": True},
            config_hash=sha256_text("second-experiment"),
        )
        session.add(second_experiment)
        await session.flush()
        second_gate = GateReport(
            skill_version_id=second_version.id,
            experiment_id=second_experiment.id,
            passed=True,
            checks=(),
        )
        second_version.gate_report_hash = second_gate.report_hash()
        second_experiment.gate_report = second_gate.model_dump(mode="json")
        second_experiment.gate_report_hash = second_gate.report_hash()
        await session.commit()
    second_publish = await service.approve(
        version_id=second_version.id,
        expected_lock_version=published.lock_version,
        reviewer="local-reviewer",
        reason="发布第二版以验证回滚",
    )
    assert second_publish.active_version_id == second_version.id
    rolled_back = await service.rollback(
        skill_id=skill.id,
        target_version_id=version.id,
        expected_lock_version=second_publish.lock_version,
        reviewer="local-reviewer",
        reason="回滚到已经通过门禁的第一版",
    )
    assert rolled_back.active_version_id == version.id
    with pytest.raises(SkillVersionConflictError):
        await service.set_status(
            skill_id=skill.id,
            target="disabled",
            expected_lock_version=0,
            reviewer="local-reviewer",
            reason="使用过期版本测试冲突",
        )
    await database.dispose()


@pytest.mark.asyncio
async def test_eval_lease_takeover_and_cancel(tmp_path: Path) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'takeover.db'}")
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    dataset, _skill, version = await prepare(database)
    coordinator = EvalCoordinator(
        database.session_factory, default_validator_registry(), lease_seconds=5
    )
    experiment = await coordinator.create_experiment(
        dataset_id=dataset.id,
        skill_version_id=version.id,
        provider="mock",
        model="mock-model",
        repeats=1,
        code_version="test",
    )
    now = datetime.now(UTC)
    first = await coordinator.claim_next("first", now=now)
    assert first is not None
    async with database.session_factory() as session:
        eval_run = await session.scalar(
            select(EvalRunRecord).where(EvalRunRecord.experiment_id == experiment.id)
        )
        assert eval_run is not None
    pending_metrics = await MetricsCollector(database.session_factory).collect_run(
        eval_run.run_id, validator_passed=False
    )
    assert pending_metrics.total_tokens is None
    second = await coordinator.claim_next("second", now=now + timedelta(seconds=6))
    assert second is not None and second.experiment_id == first.experiment_id
    cancelled = await coordinator.cancel(experiment.id)
    assert cancelled.status is EvalExperimentStatus.CANCELLED
    await database.dispose()


@pytest.mark.asyncio
async def test_correctness_regression_cannot_be_offset_by_lower_token_use(tmp_path: Path) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'regression.db'}")
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    dataset, _skill, version = await prepare(database)
    baseline = RunMetrics(
        run_id=version.id,
        status="completed",
        validator_passed=True,
        input_tokens=80,
        output_tokens=20,
        total_tokens=100,
        tool_calls=2,
        tool_succeeded=2,
        tool_failed=0,
        tool_calls_by_name={"calculator": 2},
        latency_ms=100,
        approvals=0,
        permission_denied=0,
        unknown_effects=0,
        recovery_count=0,
        skill_version_id=None,
        retrieval_score=None,
        failure_code=None,
    )
    skill_metrics = baseline.model_copy(
        update={
            "run_id": dataset.id,
            "validator_passed": False,
            "input_tokens": 10,
            "output_tokens": 10,
            "total_tokens": 20,
            "skill_version_id": version.id,
        }
    )
    pair = PairMetrics(
        case_id=dataset.id,
        case_key="regression",
        task_family="safety",
        repeat_index=0,
        comparable=True,
        baseline=baseline,
        skill=skill_metrics,
        success_delta=-1,
        token_delta=None,
        tool_call_delta=None,
        latency_delta_ms=None,
        safety_regression=False,
        efficiency_comparable=False,
    )
    experiment_config = {"scenario": "correctness-regression"}
    async with database.session_factory() as session:
        experiment = EvalExperimentRecord(
            kind=EvalExperimentKind.SKILL_COMPARISON,
            skill_version_id=version.id,
            dataset_id=dataset.id,
            status=EvalExperimentStatus.COMPLETED,
            config_snapshot=experiment_config,
            config_hash=content_hash(experiment_config),
        )
        session.add(experiment)
        await session.commit()
    family = TaskFamilyMetrics(
        task_family="safety",
        pair_count=1,
        comparable_pairs=1,
        baseline_success_rate=1,
        skill_success_rate=0,
        mean_token_delta=None,
        median_token_delta=None,
        mean_tool_call_delta=None,
        median_tool_call_delta=None,
        mean_latency_delta_ms=None,
        median_latency_delta_ms=None,
    )
    report = EvaluationReport(
        experiment_id=experiment.id,
        dataset_id=dataset.id,
        skill_version_id=version.id,
        config_hash=experiment.config_hash,
        pair_count=1,
        comparable_pairs=1,
        baseline_successes=1,
        skill_successes=0,
        baseline_success_rate=1,
        skill_success_rate=0,
        safety_regressions=0,
        efficiency_comparable_pairs=0,
        families=(family,),
        pairs=(pair,),
    )
    validators = default_validator_registry()
    gate = await QualityGate(
        database.session_factory,
        SkillDefinitionValidator(
            ToolRegistry((CalculatorTool(),)), allowed_tools=frozenset({"calculator"})
        ),
        validators,
        ArtifactService(LocalArtifactStore(tmp_path / "artifacts"), database.session_factory),
        minimum_sources=0,
    ).evaluate(report)
    assert gate.passed is False
    assert (
        next(item for item in gate.checks if item.name == "overall_success_not_regressed").passed
        is False
    )
    assert next(item for item in gate.checks if item.layer == "efficiency").passed is True
    await database.dispose()
