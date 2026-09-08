"""评测数据集、Case、实验和运行查询。"""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from evoagent.db.models import (
    EvalCaseRecord,
    EvalDatasetRecord,
    EvalExperimentRecord,
    EvalRunRecord,
)
from evoagent.db.repositories.base import RecordNotFoundError


class EvalRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def add_dataset(self, dataset: EvalDatasetRecord) -> None:
        self._session.add(dataset)

    def add_case(self, case: EvalCaseRecord) -> None:
        self._session.add(case)

    def add_experiment(self, experiment: EvalExperimentRecord) -> None:
        self._session.add(experiment)

    def add_run(self, run: EvalRunRecord) -> None:
        self._session.add(run)

    async def get_dataset(self, dataset_id: UUID) -> EvalDatasetRecord:
        record = await self._session.get(EvalDatasetRecord, dataset_id)
        if record is None:
            raise RecordNotFoundError(f"eval dataset does not exist: {dataset_id}")
        return record

    async def get_case(self, case_id: UUID) -> EvalCaseRecord:
        record = await self._session.get(EvalCaseRecord, case_id)
        if record is None:
            raise RecordNotFoundError(f"eval case does not exist: {case_id}")
        return record

    async def list_cases(self, dataset_id: UUID) -> tuple[EvalCaseRecord, ...]:
        return tuple(
            await self._session.scalars(
                select(EvalCaseRecord)
                .where(EvalCaseRecord.dataset_id == dataset_id)
                .order_by(EvalCaseRecord.case_key)
            )
        )

    async def get_run(self, eval_run_id: UUID) -> EvalRunRecord:
        record = await self._session.get(EvalRunRecord, eval_run_id)
        if record is None:
            raise RecordNotFoundError(f"eval run does not exist: {eval_run_id}")
        return record

    async def get_experiment(self, experiment_id: UUID) -> EvalExperimentRecord:
        record = await self._session.get(EvalExperimentRecord, experiment_id)
        if record is None:
            raise RecordNotFoundError(f"eval experiment does not exist: {experiment_id}")
        return record

    async def list_experiments(self) -> tuple[EvalExperimentRecord, ...]:
        return tuple(
            await self._session.scalars(
                select(EvalExperimentRecord).order_by(EvalExperimentRecord.created_at.desc())
            )
        )

    async def list_runs(self, experiment_id: UUID) -> tuple[EvalRunRecord, ...]:
        return tuple(
            await self._session.scalars(
                select(EvalRunRecord)
                .where(EvalRunRecord.experiment_id == experiment_id)
                .order_by(
                    EvalRunRecord.eval_case_id,
                    EvalRunRecord.repeat_index,
                    EvalRunRecord.mode,
                )
            )
        )
