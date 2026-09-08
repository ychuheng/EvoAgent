"""使用持久化占位和租约编排 baseline/pinned Skill 配对评测。"""

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from evoagent.db.models import (
    EvalExperimentRecord,
    EvalRunRecord,
    RunRecord,
    SessionRecord,
    SkillVersionRecord,
    TaskRecord,
)
from evoagent.db.unit_of_work import UnitOfWork
from evoagent.evals.lifecycle import (
    DatasetStatus,
    EvalExperimentKind,
    EvalExperimentStatus,
    EvalRunMode,
    EvalSplit,
)
from evoagent.evals.metrics import MetricsCollector
from evoagent.evals.schema import ValidatorSpec
from evoagent.evals.validators.base import ValidatorRegistry
from evoagent.runtime.run_config import RunConfigSnapshot, RunMode
from evoagent.skills.canonical import content_hash
from evoagent.skills.lifecycle import SkillVersionStatus
from evoagent.tasks.state_machine import PersistentRunStatus, TaskStatus
from evoagent.trace.bundle import TraceBundleService


class EvalCoordinatorError(RuntimeError):
    code = "eval_coordinator_error"


class EvalLeaseLostError(EvalCoordinatorError):
    code = "eval_lease_lost"


class EvalExperimentConfig(BaseModel):
    """只保存可重现、非敏感的整批实验配置。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    provider: str = Field(min_length=1, max_length=64)
    model: str = Field(min_length=1, max_length=256)
    repeats: int = Field(ge=1, le=100)
    pair_order: str = "alternating"
    code_version: str = Field(min_length=1, max_length=128)


@dataclass(frozen=True, slots=True)
class EvalLease:
    experiment_id: UUID
    owner: str
    expires_at: datetime


_RUN_TERMINAL = frozenset(
    {
        PersistentRunStatus.COMPLETED,
        PersistentRunStatus.FAILED,
        PersistentRunStatus.CANCELLED,
        PersistentRunStatus.TIMEOUT,
        PersistentRunStatus.LIMIT_REACHED,
    }
)


def _is_expired(expires_at: datetime, now: datetime) -> bool:
    """兼容 SQLite 丢失时区信息的测试时间戳。"""

    if (expires_at.tzinfo is None) != (now.tzinfo is None):
        expires_at = expires_at.replace(tzinfo=None)
        now = now.replace(tzinfo=None)
    return expires_at <= now


class EvalCoordinator:
    """创建配对占位、协调普通 Task，并在中断后幂等继续。"""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        validators: ValidatorRegistry,
        *,
        lease_seconds: float = 60.0,
    ) -> None:
        if lease_seconds <= 0:
            raise ValueError("eval lease seconds must be positive")
        self._session_factory = session_factory
        self._validators = validators
        self._lease_duration = timedelta(seconds=lease_seconds)

    async def create_experiment(
        self,
        *,
        dataset_id: UUID,
        skill_version_id: UUID,
        provider: str,
        model: str,
        repeats: int,
        code_version: str,
    ) -> EvalExperimentRecord:
        config = EvalExperimentConfig(
            provider=provider.strip(),
            model=model.strip(),
            repeats=repeats,
            code_version=code_version,
        )
        snapshot = config.model_dump(mode="json")
        async with UnitOfWork(self._session_factory) as unit:
            dataset = await unit.evals.get_dataset(dataset_id)
            version = await unit.session.scalar(
                select(SkillVersionRecord)
                .where(SkillVersionRecord.id == skill_version_id)
                .with_for_update()
            )
            if version is None:
                raise ValueError(f"skill version does not exist: {skill_version_id}")
            if dataset.status is not DatasetStatus.FROZEN:
                raise ValueError("comparison evaluation requires a frozen dataset")
            if version.lifecycle_status is not SkillVersionStatus.DRAFT:
                raise ValueError("only a draft version can start comparison evaluation")
            cases = tuple(
                item
                for item in await unit.evals.list_cases(dataset_id)
                if item.split is EvalSplit.HOLDOUT
            )
            if not cases:
                raise ValueError("comparison evaluation requires HOLDOUT cases")
            experiment = EvalExperimentRecord(
                kind=EvalExperimentKind.SKILL_COMPARISON,
                skill_version_id=skill_version_id,
                dataset_id=dataset_id,
                status=EvalExperimentStatus.QUEUED,
                config_snapshot=snapshot,
                config_hash=content_hash(snapshot),
            )
            unit.evals.add_experiment(experiment)
            await unit.session.flush()
            await self._ensure_pairs(unit, experiment, cases, config)
            version.lifecycle_status = SkillVersionStatus.EVALUATING
            await unit.commit()
            return experiment

    async def claim_next(self, owner: str, *, now: datetime | None = None) -> EvalLease | None:
        normalized_owner = owner.strip()
        if not normalized_owner:
            raise ValueError("eval lease owner cannot be blank")
        current = now or datetime.now(UTC)
        async with UnitOfWork(self._session_factory) as unit:
            experiment = await unit.session.scalar(
                select(EvalExperimentRecord)
                .where(
                    or_(
                        EvalExperimentRecord.status == EvalExperimentStatus.QUEUED,
                        (
                            (EvalExperimentRecord.status == EvalExperimentStatus.RUNNING)
                            & (EvalExperimentRecord.lease_expires_at <= current)
                        ),
                    )
                )
                .order_by(EvalExperimentRecord.created_at)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if experiment is None:
                return None
            experiment.status = EvalExperimentStatus.RUNNING
            experiment.lease_owner = normalized_owner
            experiment.heartbeat_at = current
            experiment.lease_expires_at = current + self._lease_duration
            await unit.commit()
            return EvalLease(experiment.id, normalized_owner, experiment.lease_expires_at)

    async def heartbeat(self, lease: EvalLease, *, now: datetime | None = None) -> EvalLease:
        current = now or datetime.now(UTC)
        expires = current + self._lease_duration
        async with UnitOfWork(self._session_factory) as unit:
            result = await unit.session.execute(
                update(EvalExperimentRecord)
                .where(
                    EvalExperimentRecord.id == lease.experiment_id,
                    EvalExperimentRecord.status == EvalExperimentStatus.RUNNING,
                    EvalExperimentRecord.lease_owner == lease.owner,
                    EvalExperimentRecord.lease_expires_at > current,
                )
                .values(heartbeat_at=current, lease_expires_at=expires)
            )
            if result.rowcount != 1:
                raise EvalLeaseLostError("evaluation lease is no longer owned")
            await unit.commit()
        return EvalLease(lease.experiment_id, lease.owner, expires)

    async def run_once(self, lease: EvalLease, *, now: datetime | None = None) -> bool:
        """处理已经终止的普通 Run；返回实验是否完成。"""

        current = now or datetime.now(UTC)
        await self._require_lease(lease, current)
        pending = await self._terminal_unvalidated_runs(lease.experiment_id)
        for eval_run in pending:
            await self._validate_and_collect(eval_run)
        async with UnitOfWork(self._session_factory) as unit:
            experiment = await unit.evals.get_experiment(lease.experiment_id)
            if experiment.lease_owner != lease.owner or experiment.lease_expires_at is None:
                raise EvalLeaseLostError("evaluation lease is no longer owned")
            eval_runs = await unit.evals.list_runs(experiment.id)
            await self._release_second_runs(unit, eval_runs, current)
            await self._mark_pair_comparability(unit, eval_runs)
            completed = all(item.metrics.get("state") == "completed" for item in eval_runs)
            if completed:
                experiment.status = EvalExperimentStatus.COMPLETED
                experiment.lease_owner = None
                experiment.lease_expires_at = None
                experiment.heartbeat_at = None
            await unit.commit()
            return completed

    async def cancel(self, experiment_id: UUID) -> EvalExperimentRecord:
        async with UnitOfWork(self._session_factory) as unit:
            experiment = await unit.evals.get_experiment(experiment_id)
            if experiment.status in (
                EvalExperimentStatus.COMPLETED,
                EvalExperimentStatus.FAILED,
                EvalExperimentStatus.CANCELLED,
            ):
                raise ValueError("terminal evaluation experiment cannot be cancelled")
            for eval_run in await unit.evals.list_runs(experiment.id):
                task = await unit.tasks.get(eval_run.task_id)
                run = await unit.runs.get(eval_run.run_id)
                if task.status in (
                    TaskStatus.QUEUED,
                    TaskStatus.PAUSED,
                    TaskStatus.WAITING_USER,
                    TaskStatus.RETRYING,
                    TaskStatus.RECOVERING,
                ):
                    task.status = TaskStatus.CANCELLED
                    run.status = PersistentRunStatus.CANCELLED
                    task.lock_version += 1
                    run.lock_version += 1
                    await unit.events.append(
                        run_id=run.id,
                        event_type="eval.run.cancelled",
                        payload={"experiment_id": str(experiment.id)},
                        created_at=datetime.now(UTC),
                    )
                elif task.status in (TaskStatus.RUNNING, TaskStatus.WAITING_TOOL):
                    task.cancel_requested = True
                    await unit.events.append(
                        run_id=run.id,
                        event_type="eval.run.cancel_requested",
                        payload={"experiment_id": str(experiment.id)},
                        created_at=datetime.now(UTC),
                    )
            experiment.status = EvalExperimentStatus.CANCELLED
            experiment.lease_owner = None
            experiment.lease_expires_at = None
            await unit.commit()
            return experiment

    async def _ensure_pairs(self, unit, experiment, cases, config) -> None:
        for case in cases:
            goal = self._case_goal(case.public_input)
            for repeat_index in range(config.repeats):
                order = (
                    (EvalRunMode.BASELINE, EvalRunMode.PINNED_SKILL)
                    if repeat_index % 2 == 0
                    else (EvalRunMode.PINNED_SKILL, EvalRunMode.BASELINE)
                )
                records: dict[EvalRunMode, EvalRunRecord] = {}
                for position, mode in enumerate(order, start=1):
                    queued = position == 1
                    session = SessionRecord(
                        title=f"Eval {experiment.id}: {case.case_key} #{repeat_index}"
                    )
                    unit.session.add(session)
                    await unit.session.flush()
                    task = TaskRecord(
                        session_id=session.id,
                        goal=goal,
                        status=TaskStatus.QUEUED if queued else TaskStatus.PAUSED,
                    )
                    unit.tasks.add(task)
                    await unit.session.flush()
                    run = RunRecord(
                        task_id=task.id,
                        status=(
                            PersistentRunStatus.QUEUED if queued else PersistentRunStatus.PAUSED
                        ),
                        provider=config.provider,
                        model=config.model,
                        run_mode=(
                            RunMode.BASELINE.value
                            if mode is EvalRunMode.BASELINE
                            else RunMode.PINNED_SKILL.value
                        ),
                        pinned_skill_version_id=(
                            experiment.skill_version_id
                            if mode is EvalRunMode.PINNED_SKILL
                            else None
                        ),
                    )
                    unit.runs.add(run)
                    await unit.session.flush()
                    await unit.events.append(
                        run_id=run.id,
                        event_type="eval.run.queued" if queued else "eval.run.waiting_pair",
                        payload={
                            "experiment_id": str(experiment.id),
                            "case_key": case.case_key,
                            "repeat_index": repeat_index,
                            "mode": mode.value,
                            "position": position,
                        },
                        created_at=datetime.now(UTC),
                    )
                    eval_run = EvalRunRecord(
                        experiment_id=experiment.id,
                        eval_case_id=case.id,
                        mode=mode,
                        repeat_index=repeat_index,
                        task_id=task.id,
                        run_id=run.id,
                        skill_version_id=(
                            experiment.skill_version_id
                            if mode is EvalRunMode.PINNED_SKILL
                            else None
                        ),
                        metrics={"state": "pending", "position": position},
                        validation_results=[],
                        passed=False,
                        comparable=True,
                    )
                    unit.evals.add_run(eval_run)
                    await unit.session.flush()
                    records[mode] = eval_run
                records[EvalRunMode.BASELINE].paired_eval_run_id = records[
                    EvalRunMode.PINNED_SKILL
                ].id
                records[EvalRunMode.PINNED_SKILL].paired_eval_run_id = records[
                    EvalRunMode.BASELINE
                ].id

    async def _require_lease(self, lease: EvalLease, now: datetime) -> None:
        async with UnitOfWork(self._session_factory) as unit:
            experiment = await unit.evals.get_experiment(lease.experiment_id)
            if (
                experiment.status is not EvalExperimentStatus.RUNNING
                or experiment.lease_owner != lease.owner
                or experiment.lease_expires_at is None
                or _is_expired(experiment.lease_expires_at, now)
            ):
                raise EvalLeaseLostError("evaluation lease is no longer owned")

    async def _terminal_unvalidated_runs(self, experiment_id: UUID) -> tuple[EvalRunRecord, ...]:
        async with UnitOfWork(self._session_factory) as unit:
            rows = await unit.session.execute(
                select(EvalRunRecord, RunRecord)
                .join(RunRecord, RunRecord.id == EvalRunRecord.run_id)
                .where(EvalRunRecord.experiment_id == experiment_id)
            )
            return tuple(
                eval_run
                for eval_run, run in rows
                if eval_run.metrics.get("state") != "completed" and run.status in _RUN_TERMINAL
            )

    async def _validate_and_collect(self, eval_run: EvalRunRecord) -> None:
        async with UnitOfWork(self._session_factory) as unit:
            case = await unit.evals.get_case(eval_run.eval_case_id)
            specs = tuple(ValidatorSpec.model_validate(item) for item in case.private_validators)
        trace = await TraceBundleService(self._session_factory).build(eval_run.run_id)
        results = tuple(self._validators.run(spec, trace) for spec in specs)
        passed = all(item.passed for item in results)
        metrics = await MetricsCollector(self._session_factory).collect_run(
            eval_run.run_id, validator_passed=passed
        )
        async with UnitOfWork(self._session_factory) as unit:
            current = await unit.evals.get_run(eval_run.id)
            if current.metrics.get("state") == "completed":
                return
            current.passed = passed
            current.validation_results = [item.model_dump(mode="json") for item in results]
            current.metrics = {
                **current.metrics,
                "state": "completed",
                "run_metrics": metrics.model_dump(mode="json"),
            }
            await unit.commit()

    async def _release_second_runs(self, unit, eval_runs, now: datetime) -> None:
        by_pair: dict[tuple[UUID, int], list[EvalRunRecord]] = {}
        for item in eval_runs:
            by_pair.setdefault((item.eval_case_id, item.repeat_index), []).append(item)
        for pair in by_pair.values():
            first = next(item for item in pair if item.metrics.get("position") == 1)
            second = next(item for item in pair if item.metrics.get("position") == 2)
            if first.metrics.get("state") != "completed":
                continue
            task = await unit.tasks.get(second.task_id)
            run = await unit.runs.get(second.run_id)
            if task.status is TaskStatus.PAUSED and run.status is PersistentRunStatus.PAUSED:
                task.status = TaskStatus.QUEUED
                run.status = PersistentRunStatus.QUEUED
                task.lock_version += 1
                run.lock_version += 1
                await unit.events.append(
                    run_id=run.id,
                    event_type="eval.pair.released",
                    payload={"paired_eval_run_id": str(first.id)},
                    created_at=now,
                )

    @staticmethod
    async def _mark_pair_comparability(unit, eval_runs) -> None:
        by_pair: dict[tuple[UUID, int], list[EvalRunRecord]] = {}
        for item in eval_runs:
            by_pair.setdefault((item.eval_case_id, item.repeat_index), []).append(item)
        for pair in by_pair.values():
            if len(pair) != 2 or any(item.metrics.get("state") != "completed" for item in pair):
                continue
            configs: list[RunConfigSnapshot] = []
            for item in pair:
                run = await unit.runs.get(item.run_id)
                if run.config_snapshot is not None:
                    configs.append(RunConfigSnapshot.model_validate(run.config_snapshot))
            comparable = len(configs) == 2 and configs[0].comparable_with(configs[1])
            for item in pair:
                item.comparable = comparable

    @staticmethod
    def _case_goal(public_input: dict[str, object]) -> str:
        goal = public_input.get("goal")
        if isinstance(goal, str) and goal.strip():
            return goal.strip()
        return json.dumps(public_input, ensure_ascii=False, sort_keys=True)
