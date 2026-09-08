"""从持久化 Run 构造可解释的配对指标与不可变评测报告。"""

from collections import Counter, defaultdict
from datetime import datetime
from statistics import mean, median
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from evoagent.db.models import (
    ApprovalStatus,
    ArtifactRecord,
    EvalCaseRecord,
    EvalRunRecord,
    RunEventRecord,
    RunSkillSelectionRecord,
    SkillVersionRecord,
    ToolApprovalRecord,
    ToolCallRecord,
    ToolCallStatus,
    ToolEffectRecord,
    ToolEffectStatus,
    TurnRecord,
)
from evoagent.db.unit_of_work import UnitOfWork
from evoagent.evals.lifecycle import EvalRunMode
from evoagent.skills.canonical import canonical_json, content_hash
from evoagent.trace.artifacts import ArtifactService


class RunMetrics(BaseModel):
    """单次 EvalRun 的原始指标；未知值保持为 ``None``。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    run_id: UUID
    status: str
    validator_passed: bool
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None
    tool_calls: int
    tool_succeeded: int
    tool_failed: int
    tool_calls_by_name: dict[str, int]
    latency_ms: float | None
    approvals: int
    permission_denied: int
    unknown_effects: int
    recovery_count: int
    skill_version_id: UUID | None
    retrieval_score: float | None
    failure_code: str | None


class PairMetrics(BaseModel):
    """同一 Case 和 repeat 的 baseline/skill 对照。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    case_id: UUID
    case_key: str
    task_family: str
    repeat_index: int
    comparable: bool
    baseline: RunMetrics
    skill: RunMetrics
    success_delta: int
    token_delta: int | None
    tool_call_delta: int | None
    latency_delta_ms: float | None
    safety_regression: bool
    efficiency_comparable: bool


class TaskFamilyMetrics(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    task_family: str
    pair_count: int
    comparable_pairs: int
    baseline_success_rate: float
    skill_success_rate: float
    mean_token_delta: float | None
    median_token_delta: float | None
    mean_tool_call_delta: float | None
    median_tool_call_delta: float | None
    mean_latency_delta_ms: float | None
    median_latency_delta_ms: float | None


class EvaluationReport(BaseModel):
    """保留样本明细和分组汇总，不声称小样本具有统计显著性。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: int = 1
    experiment_id: UUID
    dataset_id: UUID
    skill_version_id: UUID
    config_hash: str
    pair_count: int
    comparable_pairs: int
    baseline_successes: int
    skill_successes: int
    baseline_success_rate: float
    skill_success_rate: float
    safety_regressions: int
    efficiency_comparable_pairs: int
    families: tuple[TaskFamilyMetrics, ...]
    pairs: tuple[PairMetrics, ...]

    def report_hash(self) -> str:
        return content_hash(self.model_dump(mode="json"))


def _duration_ms(started: datetime | None, ended: datetime | None) -> float | None:
    if started is None or ended is None:
        return None
    # SQLite 可能丢失时区信息；同一数据库写入的两端仍可安全相减。
    if (started.tzinfo is None) != (ended.tzinfo is None):
        started = started.replace(tzinfo=None)
        ended = ended.replace(tzinfo=None)
    return max(0.0, (ended - started).total_seconds() * 1000)


class MetricsCollector:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def collect_run(self, run_id: UUID, *, validator_passed: bool) -> RunMetrics:
        async with UnitOfWork(self._session_factory) as unit:
            run = await unit.runs.get(run_id)
            turns = tuple(
                await unit.session.scalars(select(TurnRecord).where(TurnRecord.run_id == run_id))
            )
            calls = tuple(
                await unit.session.scalars(
                    select(ToolCallRecord).where(ToolCallRecord.run_id == run_id)
                )
            )
            call_ids = [item.id for item in calls]
            effects = (
                tuple(
                    await unit.session.scalars(
                        select(ToolEffectRecord).where(ToolEffectRecord.tool_call_id.in_(call_ids))
                    )
                )
                if call_ids
                else ()
            )
            approvals = (
                tuple(
                    await unit.session.scalars(
                        select(ToolApprovalRecord).where(
                            ToolApprovalRecord.tool_call_id.in_(call_ids)
                        )
                    )
                )
                if call_ids
                else ()
            )
            events = tuple(
                await unit.session.scalars(
                    select(RunEventRecord).where(RunEventRecord.run_id == run_id)
                )
            )
            selection = await unit.session.scalar(
                select(RunSkillSelectionRecord)
                .where(RunSkillSelectionRecord.run_id == run_id)
                .order_by(RunSkillSelectionRecord.rank)
                .limit(1)
            )

        usage_rows = [item.usage for item in turns]
        usage_known = bool(usage_rows) and all(item is not None for item in usage_rows)
        input_tokens = (
            sum(int(item["input_tokens"]) for item in usage_rows if item is not None)
            if usage_known
            else None
        )
        output_tokens = (
            sum(int(item["output_tokens"]) for item in usage_rows if item is not None)
            if usage_known
            else None
        )
        total_tokens = (
            sum(int(item["total_tokens"]) for item in usage_rows if item is not None)
            if usage_known
            else None
        )
        distribution = Counter(item.tool_name for item in calls)
        return RunMetrics(
            run_id=run.id,
            status=run.status.value,
            validator_passed=validator_passed,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            tool_calls=len(calls),
            tool_succeeded=sum(item.status is ToolCallStatus.SUCCEEDED for item in calls),
            tool_failed=sum(
                item.status in (ToolCallStatus.FAILED, ToolCallStatus.UNKNOWN) for item in calls
            ),
            tool_calls_by_name=dict(sorted(distribution.items())),
            latency_ms=_duration_ms(run.started_at, run.ended_at),
            approvals=sum(item.status is not ApprovalStatus.CANCELLED for item in approvals),
            permission_denied=sum(item.status is ToolCallStatus.DENIED for item in calls),
            unknown_effects=sum(item.status is ToolEffectStatus.UNKNOWN for item in effects),
            recovery_count=sum(item.event_type == "recovery.started" for item in events),
            skill_version_id=selection.skill_version_id if selection is not None else None,
            retrieval_score=selection.score if selection is not None else None,
            failure_code=run.error_code,
        )


def _optional_delta(skill: int | float | None, baseline: int | float | None):
    if skill is None or baseline is None:
        return None
    return skill - baseline


def _aggregate_family(name: str, pairs: list[PairMetrics]) -> TaskFamilyMetrics:
    efficient = [item for item in pairs if item.efficiency_comparable]

    def values(field: str) -> list[float]:
        return [float(value) for item in efficient if (value := getattr(item, field)) is not None]

    def average(items: list[float]) -> float | None:
        return mean(items) if items else None

    def middle(items: list[float]) -> float | None:
        return median(items) if items else None

    token = values("token_delta")
    tools = values("tool_call_delta")
    latency = values("latency_delta_ms")
    return TaskFamilyMetrics(
        task_family=name,
        pair_count=len(pairs),
        comparable_pairs=sum(item.comparable for item in pairs),
        baseline_success_rate=sum(item.baseline.validator_passed for item in pairs) / len(pairs),
        skill_success_rate=sum(item.skill.validator_passed for item in pairs) / len(pairs),
        mean_token_delta=average(token),
        median_token_delta=middle(token),
        mean_tool_call_delta=average(tools),
        median_tool_call_delta=middle(tools),
        mean_latency_delta_ms=average(latency),
        median_latency_delta_ms=middle(latency),
    )


class EvaluationReportService:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def build(self, experiment_id: UUID) -> EvaluationReport:
        async with UnitOfWork(self._session_factory) as unit:
            experiment = await unit.evals.get_experiment(experiment_id)
            eval_runs = await unit.evals.list_runs(experiment_id)
            cases = {item.id: item for item in await unit.evals.list_cases(experiment.dataset_id)}
        grouped: dict[tuple[UUID, int], dict[EvalRunMode, EvalRunRecord]] = defaultdict(dict)
        for item in eval_runs:
            grouped[(item.eval_case_id, item.repeat_index)][item.mode] = item

        pairs: list[PairMetrics] = []
        for (case_id, repeat_index), modes in sorted(
            grouped.items(), key=lambda item: (cases[item[0][0]].case_key, item[0][1])
        ):
            if set(modes) != {EvalRunMode.BASELINE, EvalRunMode.PINNED_SKILL}:
                raise ValueError("evaluation experiment contains an incomplete pair")
            baseline_record = modes[EvalRunMode.BASELINE]
            skill_record = modes[EvalRunMode.PINNED_SKILL]
            baseline = await self._run_metrics(baseline_record)
            skill = await self._run_metrics(skill_record)
            comparable = baseline_record.comparable and skill_record.comparable
            both_passed = baseline.validator_passed and skill.validator_passed
            usage_known = baseline.total_tokens is not None and skill.total_tokens is not None
            efficiency_comparable = comparable and both_passed and usage_known
            case: EvalCaseRecord = cases[case_id]
            pairs.append(
                PairMetrics(
                    case_id=case_id,
                    case_key=case.case_key,
                    task_family=case.task_family,
                    repeat_index=repeat_index,
                    comparable=comparable,
                    baseline=baseline,
                    skill=skill,
                    success_delta=int(skill.validator_passed) - int(baseline.validator_passed),
                    token_delta=(
                        int(_optional_delta(skill.total_tokens, baseline.total_tokens))
                        if efficiency_comparable
                        else None
                    ),
                    tool_call_delta=(
                        skill.tool_calls - baseline.tool_calls
                        if comparable and both_passed
                        else None
                    ),
                    latency_delta_ms=(
                        float(value)
                        if comparable
                        and both_passed
                        and (value := _optional_delta(skill.latency_ms, baseline.latency_ms))
                        is not None
                        else None
                    ),
                    safety_regression=(
                        skill.permission_denied > baseline.permission_denied
                        or skill.unknown_effects > baseline.unknown_effects
                    ),
                    efficiency_comparable=efficiency_comparable,
                )
            )

        if experiment.skill_version_id is None:
            raise ValueError("comparison experiment has no skill version")
        by_family: dict[str, list[PairMetrics]] = defaultdict(list)
        for pair in pairs:
            by_family[pair.task_family].append(pair)
        return EvaluationReport(
            experiment_id=experiment.id,
            dataset_id=experiment.dataset_id,
            skill_version_id=experiment.skill_version_id,
            config_hash=experiment.config_hash,
            pair_count=len(pairs),
            comparable_pairs=sum(item.comparable for item in pairs),
            baseline_successes=sum(item.baseline.validator_passed for item in pairs),
            skill_successes=sum(item.skill.validator_passed for item in pairs),
            baseline_success_rate=(
                sum(item.baseline.validator_passed for item in pairs) / len(pairs) if pairs else 0.0
            ),
            skill_success_rate=(
                sum(item.skill.validator_passed for item in pairs) / len(pairs) if pairs else 0.0
            ),
            safety_regressions=sum(item.safety_regression for item in pairs),
            efficiency_comparable_pairs=sum(item.efficiency_comparable for item in pairs),
            families=tuple(
                _aggregate_family(name, items) for name, items in sorted(by_family.items())
            ),
            pairs=tuple(pairs),
        )

    async def freeze(
        self, experiment_id: UUID, artifacts: ArtifactService
    ) -> tuple[EvaluationReport, str, UUID]:
        async with UnitOfWork(self._session_factory) as unit:
            experiment = await unit.evals.get_experiment(experiment_id)
            if experiment.report_artifact_id is not None:
                artifact = await unit.session.get(ArtifactRecord, experiment.report_artifact_id)
                if artifact is None or experiment.report_hash is None:
                    raise ValueError("evaluation report reference is incomplete")
                raw = await artifacts.read(artifact.uri)
                report = EvaluationReport.model_validate_json(raw)
                if report.report_hash() != experiment.report_hash:
                    raise ValueError("frozen evaluation report was modified")
                return report, experiment.report_hash, artifact.id

        report = await self.build(experiment_id)
        raw = canonical_json(report.model_dump(mode="json")).encode()
        async with UnitOfWork(self._session_factory) as unit:
            experiment = await unit.evals.get_experiment(experiment_id)
            runs = await unit.evals.list_runs(experiment_id)
            if not runs:
                raise ValueError("evaluation experiment has no runs")
        artifact = await artifacts.create_unique(
            run_id=runs[0].run_id,
            name=f"eval-report-{experiment_id}.json",
            content=raw,
            artifact_type="application/vnd.evoagent.eval-report+json",
            attributes={"experiment_id": str(experiment_id), "immutable": True},
        )
        digest = report.report_hash()
        async with UnitOfWork(self._session_factory) as unit:
            experiment = await unit.evals.get_experiment(experiment_id)
            if experiment.report_artifact_id is not None:
                raise ValueError("evaluation report was concurrently frozen")
            experiment.report_artifact_id = artifact.id
            experiment.report_hash = digest
            version: SkillVersionRecord | None = await unit.session.get(
                SkillVersionRecord, experiment.skill_version_id
            )
            if version is not None:
                version.evaluation_report_hash = digest
            await unit.commit()
        return report, digest, artifact.id

    async def _run_metrics(self, record: EvalRunRecord) -> RunMetrics:
        saved: Any = record.metrics.get("run_metrics")
        if saved is not None:
            return RunMetrics.model_validate(saved)
        return await MetricsCollector(self._session_factory).collect_run(
            record.run_id, validator_passed=record.passed
        )
