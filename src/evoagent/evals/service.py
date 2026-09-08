"""对已有训练 Run 执行来源资格验证。"""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from evoagent.db.models import EvalExperimentRecord, EvalRunRecord
from evoagent.db.unit_of_work import UnitOfWork
from evoagent.evals.lifecycle import (
    DatasetStatus,
    EvalExperimentKind,
    EvalExperimentStatus,
    EvalRunMode,
    EvalSplit,
)
from evoagent.evals.schema import ValidatorSpec
from evoagent.evals.validators.base import ValidatorRegistry
from evoagent.skills.canonical import content_hash
from evoagent.tasks.state_machine import PersistentRunStatus
from evoagent.trace.bundle import TraceBundleService


class SourceValidationError(ValueError):
    """目标 Run 或 Case 不满足来源验证前置条件。"""


class SourceValidationService:
    def __init__(
        self, session_factory: async_sessionmaker[AsyncSession], validators: ValidatorRegistry
    ) -> None:
        self._session_factory = session_factory
        self._validators = validators

    async def validate(self, *, run_id: UUID, eval_case_id: UUID) -> EvalRunRecord:
        async with UnitOfWork(self._session_factory) as unit:
            existing = await unit.session.scalar(
                select(EvalRunRecord).where(EvalRunRecord.run_id == run_id)
            )
            if existing is not None:
                if existing.eval_case_id != eval_case_id:
                    raise SourceValidationError("run is already bound to another eval case")
                return existing
            run = await unit.runs.get(run_id)
            case = await unit.evals.get_case(eval_case_id)
            dataset = await unit.evals.get_dataset(case.dataset_id)
            if run.status is not PersistentRunStatus.COMPLETED:
                raise SourceValidationError("only completed runs can be validated")
            if case.split is not EvalSplit.TRAIN:
                raise SourceValidationError("source validation only accepts TRAIN cases")
            if dataset.status is not DatasetStatus.FROZEN:
                raise SourceValidationError("source validation requires a frozen dataset")
            validators = tuple(
                ValidatorSpec.model_validate(item) for item in case.private_validators
            )

        trace = await TraceBundleService(self._session_factory).build(run_id)
        results = tuple(self._validators.run(spec, trace) for spec in validators)
        passed = all(result.passed for result in results)
        snapshot = {"validator_versions": [f"{item.name}@{item.version}" for item in validators]}
        async with UnitOfWork(self._session_factory) as unit:
            experiment = EvalExperimentRecord(
                kind=EvalExperimentKind.SOURCE_VALIDATION,
                dataset_id=case.dataset_id,
                status=EvalExperimentStatus.COMPLETED,
                config_snapshot=snapshot,
                config_hash=content_hash(snapshot),
                gate_report={"passed": passed},
            )
            unit.evals.add_experiment(experiment)
            await unit.session.flush()
            eval_run = EvalRunRecord(
                experiment_id=experiment.id,
                eval_case_id=case.id,
                mode=EvalRunMode.BASELINE,
                task_id=run.task_id,
                run_id=run.id,
                metrics={"validator_count": len(results)},
                validation_results=[result.model_dump(mode="json") for result in results],
                passed=passed,
                comparable=True,
            )
            unit.evals.add_run(eval_run)
            await unit.commit()
            return eval_run
