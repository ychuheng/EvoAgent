"""评测数据集的安全加载、导入、内容寻址与冻结服务。"""

import json
from pathlib import Path
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from evoagent.db.models import EvalCaseRecord, EvalDatasetRecord
from evoagent.db.unit_of_work import UnitOfWork
from evoagent.evals.lifecycle import DatasetStatus, ensure_dataset_transition
from evoagent.evals.schema import EvalDatasetDefinition
from evoagent.skills.canonical import content_hash


class DatasetConflictError(ValueError):
    """同名同版本数据集已经对应另一份内容。"""


def load_dataset_definition(path: Path, *, root: Path) -> EvalDatasetDefinition:
    """只从配置的数据集根目录读取 UTF-8 JSON，不跟随越界路径。"""

    resolved_root = root.expanduser().resolve(strict=True)
    resolved_path = path.expanduser()
    if not resolved_path.is_absolute():
        resolved_path = resolved_root / resolved_path
    resolved_path = resolved_path.resolve(strict=True)
    if not resolved_path.is_relative_to(resolved_root):
        raise ValueError("dataset path escapes configured root")
    if resolved_path.suffix.lower() != ".json" or not resolved_path.is_file():
        raise ValueError("dataset must be a JSON file")
    return EvalDatasetDefinition.model_validate(json.loads(resolved_path.read_text("utf-8")))


class EvalDatasetService:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def import_definition(self, definition: EvalDatasetDefinition) -> EvalDatasetRecord:
        digest = content_hash(definition.model_dump(mode="json"))
        async with UnitOfWork(self._session_factory) as unit:
            existing = await unit.session.scalar(
                select(EvalDatasetRecord).where(
                    EvalDatasetRecord.name == definition.name,
                    EvalDatasetRecord.version == definition.version,
                )
            )
            if existing is not None:
                if existing.content_hash != digest:
                    raise DatasetConflictError("dataset version is immutable")
                return existing
            dataset = EvalDatasetRecord(
                name=definition.name,
                version=definition.version,
                content_hash=digest,
                status=DatasetStatus.DRAFT,
            )
            unit.evals.add_dataset(dataset)
            await unit.session.flush()
            for case in definition.cases:
                unit.evals.add_case(
                    EvalCaseRecord(
                        dataset_id=dataset.id,
                        case_key=case.case_key,
                        task_family=case.task_family,
                        split=case.split,
                        public_input=case.public_input,
                        private_validators=[
                            spec.model_dump(mode="json") for spec in case.private_validators
                        ],
                        risk_profile=case.risk_profile,
                    )
                )
            await unit.commit()
            return dataset

    async def freeze(self, dataset_id: UUID) -> EvalDatasetRecord:
        async with UnitOfWork(self._session_factory) as unit:
            dataset = await unit.evals.get_dataset(dataset_id)
            ensure_dataset_transition(dataset.status, DatasetStatus.FROZEN)
            dataset.status = DatasetStatus.FROZEN
            await unit.commit()
            return dataset
