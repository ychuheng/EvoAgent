"""使用持久化占位和租约编排 baseline/pinned Skill 配对评测。"""

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter
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
from evoagent.skills.schema import TaskFamily
from evoagent.tasks.lease_guard import database_now
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
    epoch: int = 0


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
            if dataset.purpose != "formal":
                raise ValueError("formal evaluation rejects personal development datasets")
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

    async def create_personal_validation(
        self,
        *,
        learning_request_id,
        dataset_id,
        candidate_version_id,
        comparison_version_id=None,
        provider,
        model,
        repeats,
        code_version,
        job_guard,
        complete_stage=None,
        replicas=None,
    ):
        # This coordinator is not a payment authorization boundary. Until the
        # task dispatch reservation adapter is wired, only offline runs enter.
        if provider != "mock":
            raise EvalCoordinatorError("personal_paid_dispatch_not_connected")
        config = EvalExperimentConfig(
            provider=provider, model=model, repeats=repeats, code_version=code_version
        )
        async with UnitOfWork(self._session_factory) as unit:
            from evoagent.db.models import LearningRequestRecord
            from evoagent.learning.sources import PersonalSourceService

            initial = await unit.session.get(LearningRequestRecord, learning_request_id)
            if initial is None:
                raise EvalCoordinatorError("personal_validation_request_missing")
            await PersonalSourceService(self._session_factory)._lock_run_scope(
                unit.session, initial.origin_run_id
            )
            _, request = await job_guard.check(unit.session)
            if (
                request.id != learning_request_id
                or request.request_kind != "validate"
                or request.candidate_version_id != candidate_version_id
                or request.base_version_id != comparison_version_id
            ):
                raise EvalCoordinatorError("personal_validation_identity_conflict")
            if request.validation_experiment_id is not None:
                existing = await unit.evals.get_experiment(request.validation_experiment_id)
                if (
                    existing.purpose != "personal_validation"
                    or existing.dataset_id != dataset_id
                    or existing.skill_version_id != candidate_version_id
                    or existing.comparison_version_id != comparison_version_id
                    or existing.learning_request_id != request.id
                    or existing.config_hash != content_hash(config.model_dump(mode="json"))
                ):
                    raise EvalCoordinatorError("personal_validation_config_conflict")
                return existing
            dataset = await unit.evals.get_dataset(dataset_id)
            if (
                dataset.status is not DatasetStatus.FROZEN
                or dataset.purpose != "personal_dev"
                or dataset.content_hash
                != request.frozen_inputs.get("validation_input_manifest_hash")
            ):
                raise EvalCoordinatorError("personal_validation_dataset_required")
            from evoagent.skills.access import SkillAccessPolicy

            version = await SkillAccessPolicy().check(
                unit.session,
                candidate_version_id,
                workspace_id=request.workspace_id,
                project_id=request.project_id,
            )
            if version.lifecycle_status is SkillVersionStatus.REJECTED:
                raise EvalCoordinatorError("personal_validation_candidate_rejected")
            if comparison_version_id is not None:
                control = await SkillAccessPolicy().check(
                    unit.session,
                    comparison_version_id,
                    workspace_id=request.workspace_id,
                    project_id=request.project_id,
                )
                if control.skill_id != version.skill_id:
                    raise EvalCoordinatorError("personal_validation_control_skill_mismatch")
            cases = await unit.evals.list_cases(dataset.id)
            if not cases or any(case.split is not EvalSplit.TRAIN for case in cases):
                raise EvalCoordinatorError("personal_validation_rejects_holdout")
            experiment = EvalExperimentRecord(
                purpose="personal_validation",
                kind=EvalExperimentKind.SKILL_COMPARISON,
                skill_version_id=candidate_version_id,
                comparison_version_id=comparison_version_id,
                learning_request_id=request.id,
                dataset_id=dataset.id,
                status=EvalExperimentStatus.QUEUED,
                config_snapshot=config.model_dump(mode="json"),
                config_hash=content_hash(config.model_dump(mode="json")),
            )
            unit.evals.add_experiment(experiment)
            await unit.session.flush()
            await self._ensure_pairs(unit, experiment, cases, config, replicas=replicas)
            request.validation_experiment_id = experiment.id
            # Formal lifecycle/gate hashes are deliberately untouched.
            await job_guard.check(unit.session)
            if complete_stage is not None:
                await complete_stage(unit.session, request, experiment)
            await unit.commit()
            return experiment

    async def claim_next(self, owner: str, *, now: datetime | None = None) -> EvalLease | None:
        normalized_owner = owner.strip()
        if not normalized_owner:
            raise ValueError("eval lease owner cannot be blank")
        async with UnitOfWork(self._session_factory) as unit:
            current = now or await database_now(unit.session)
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
            experiment.lease_epoch += 1
            experiment.heartbeat_at = current
            experiment.lease_expires_at = current + self._lease_duration
            await unit.commit()
            return EvalLease(
                experiment.id, normalized_owner, experiment.lease_expires_at, experiment.lease_epoch
            )

    async def heartbeat(self, lease: EvalLease, *, now: datetime | None = None) -> EvalLease:
        async with UnitOfWork(self._session_factory) as unit:
            current = now or await database_now(unit.session)
            expires = current + self._lease_duration
            result = await unit.session.execute(
                update(EvalExperimentRecord)
                .where(
                    EvalExperimentRecord.id == lease.experiment_id,
                    EvalExperimentRecord.status == EvalExperimentStatus.RUNNING,
                    EvalExperimentRecord.lease_owner == lease.owner,
                    EvalExperimentRecord.lease_epoch == lease.epoch,
                    EvalExperimentRecord.lease_expires_at > current,
                )
                .values(heartbeat_at=current, lease_expires_at=expires)
            )
            if result.rowcount != 1:
                raise EvalLeaseLostError("evaluation lease is no longer owned")
            await unit.commit()
        return EvalLease(lease.experiment_id, lease.owner, expires, lease.epoch)

    async def run_once(self, lease: EvalLease, *, now: datetime | None = None) -> bool:
        """处理已经终止的普通 Run；返回实验是否完成。"""

        current = now or datetime.now(UTC)
        await self._require_lease(lease, now)
        pending = await self._terminal_unvalidated_runs(lease.experiment_id)
        for eval_run in pending:
            await self._validate_and_collect(eval_run, lease, now)
        async with UnitOfWork(self._session_factory) as unit:
            experiment = await self._guard(unit.session, lease, now)
            eval_runs = await unit.evals.list_runs(experiment.id)
            await self._release_second_runs(unit, eval_runs, current)
            await self._mark_pair_comparability(unit, eval_runs)
            completed = all(item.metrics.get("state") == "completed" for item in eval_runs)
            if completed:
                experiment.status = EvalExperimentStatus.COMPLETED
                experiment.lease_owner = None
                experiment.lease_expires_at = None
                experiment.heartbeat_at = None
                if experiment.purpose == "personal_validation":
                    from evoagent.db.models import MaintenanceJobRecord

                    dedupe = f"validation-completed:{experiment.id}"
                    if not await unit.session.scalar(
                        select(MaintenanceJobRecord.id).where(
                            MaintenanceJobRecord.dedupe_key == dedupe
                        )
                    ):
                        # Durable completion outbox: no origin Request lock in
                        # this transaction, which also updates paired Tasks.
                        unit.session.add(
                            MaintenanceJobRecord(
                                dedupe_key=dedupe,
                                kind="learning_validation_completed",
                                priority=100,
                                payload={
                                    "request_id": str(experiment.learning_request_id),
                                    "experiment_id": str(experiment.id),
                                },
                            )
                        )
            await unit.commit()
            return completed

    async def cancel(self, experiment_id: UUID) -> EvalExperimentRecord:
        async with UnitOfWork(self._session_factory) as unit:
            experiment = await unit.session.scalar(
                select(EvalExperimentRecord)
                .where(EvalExperimentRecord.id == experiment_id)
                .with_for_update()
            )
            if experiment is None:
                raise ValueError("evaluation experiment does not exist")
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

    async def _ensure_pairs(self, unit, experiment, cases, config, *, replicas=None) -> None:
        personal_workspace_id = None
        if experiment.purpose == "personal_validation":
            from evoagent.db.models import LearningRequestRecord

            request = await unit.session.get(LearningRequestRecord, experiment.learning_request_id)
            personal_workspace_id = request.workspace_id
            fixture_cases = {
                item["case_key"]
                for item in request.frozen_inputs.get("validation_cases", [])
                if item.get("fixture_id") is not None
            }
            expected_replicas = {
                (case_key, arm, repeat)
                for case_key in fixture_cases
                for arm in ("control", "treatment")
                for repeat in range(config.repeats)
            }
            if set(replicas or {}) != expected_replicas:
                raise EvalCoordinatorError("personal_validation_replicas_incomplete")
        for case in cases:
            family = None
            if experiment.purpose == "personal_validation":
                try:
                    family = TypeAdapter(TaskFamily).validate_python(case.task_family)
                except ValueError:
                    raise EvalCoordinatorError("personal_validation_task_family_invalid") from None
            goal = (
                json.dumps(case.public_input, ensure_ascii=False, sort_keys=True)
                if experiment.purpose == "personal_validation"
                else self._case_goal(case.public_input)
            )
            for repeat_index in range(config.repeats):
                order = (
                    ("control", "treatment") if repeat_index % 2 == 0 else ("treatment", "control")
                )
                records: dict[str, EvalRunRecord] = {}
                for position, arm in enumerate(order, start=1):
                    selected_version = (
                        experiment.skill_version_id
                        if arm == "treatment"
                        else experiment.comparison_version_id
                    )
                    mode = (
                        EvalRunMode.PINNED_SKILL
                        if selected_version is not None
                        else EvalRunMode.BASELINE
                    )
                    queued = position == 1
                    session = SessionRecord(
                        title=f"Eval {experiment.id}: {case.case_key} #{repeat_index}",
                        **(
                            {"workspace_id": personal_workspace_id}
                            if personal_workspace_id is not None
                            else {}
                        ),
                    )
                    unit.session.add(session)
                    await unit.session.flush()
                    task = TaskRecord(
                        session_id=session.id,
                        goal=goal,
                        family=family,
                        status=TaskStatus.QUEUED if queued else TaskStatus.PAUSED,
                    )
                    unit.tasks.add(task)
                    await unit.session.flush()
                    run = RunRecord(
                        data_role="dev"
                        if experiment.purpose == "personal_validation"
                        else case.split.value,
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
                        pinned_skill_version_id=selected_version,
                    )
                    unit.runs.add(run)
                    await unit.session.flush()
                    from evoagent.sessions.service import append_message

                    message = await append_message(
                        unit.session, task=task, run=run, kind="goal", role="user", content=goal
                    )
                    task.history_before_sequence = message.session_sequence
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
                        arm=arm,
                        repeat_index=repeat_index,
                        task_id=task.id,
                        run_id=run.id,
                        skill_version_id=selected_version,
                        metrics={"state": "pending", "position": position},
                        validation_results=[],
                        passed=False,
                        comparable=True,
                    )
                    unit.evals.add_run(eval_run)
                    await unit.session.flush()
                    records[arm] = eval_run
                    if replicas and (case.case_key, arm, repeat_index) in replicas:
                        from evoagent.db.models import ValidationReplicaBindingRecord

                        replica = replicas[case.case_key, arm, repeat_index]
                        unit.session.add(
                            ValidationReplicaBindingRecord(
                                workspace_id=personal_workspace_id,
                                request_id=request.id,
                                eval_run_id=eval_run.id,
                                run_id=run.id,
                                case_key=case.case_key,
                                arm=arm,
                                repeat_index=repeat_index,
                                fixture_id=replica.input_manifest["fixture_id"],
                                input_fingerprint=request.frozen_inputs["input_fingerprints"][
                                    case.case_key
                                ],
                                manifest=replica.binding_manifest,
                                manifest_hash=content_hash(replica.binding_manifest),
                            )
                        )
                records["control"].paired_eval_run_id = records["treatment"].id
                records["treatment"].paired_eval_run_id = records["control"].id

    async def _require_lease(self, lease: EvalLease, now: datetime) -> None:
        async with UnitOfWork(self._session_factory) as unit:
            await self._guard(unit.session, lease, now)

    async def _guard(self, session, lease, now=None):
        current = now or await database_now(session)
        experiment = await session.scalar(
            select(EvalExperimentRecord)
            .where(
                EvalExperimentRecord.id == lease.experiment_id,
                EvalExperimentRecord.status == EvalExperimentStatus.RUNNING,
                EvalExperimentRecord.lease_owner == lease.owner,
                EvalExperimentRecord.lease_epoch == lease.epoch,
                EvalExperimentRecord.lease_expires_at > current,
            )
            .with_for_update()
        )
        if experiment is None:
            raise EvalLeaseLostError("evaluation lease is no longer owned")
        return experiment

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

    async def _validate_and_collect(self, eval_run: EvalRunRecord, lease, now=None) -> None:
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
            await self._guard(unit.session, lease, now)
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
