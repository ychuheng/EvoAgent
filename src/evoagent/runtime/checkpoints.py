"""版本化 LoopState 快照的保存与加载。"""

from datetime import UTC, datetime
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from evoagent.core.models import LoopState
from evoagent.db.models import RunSnapshotRecord
from evoagent.db.unit_of_work import UnitOfWork


class SnapshotCompatibilityError(ValueError):
    """快照版本或内容不受当前运行时支持。"""


class PersistentCheckpointStore:
    """把快照事件和状态放在同一事务中提交。"""

    def __init__(
        self,
        run_id: UUID,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        schema_version: int = 1,
    ) -> None:
        if schema_version < 1:
            raise ValueError("snapshot schema version must be positive")
        self._run_id = run_id
        self._session_factory = session_factory
        self._schema_version = schema_version

    async def save(self, state: LoopState) -> None:
        async with UnitOfWork(self._session_factory) as unit:
            event = await unit.events.append(
                run_id=self._run_id,
                event_type="snapshot.saved",
                payload={
                    "completed_iterations": state.completed_iterations,
                    "schema_version": self._schema_version,
                },
                created_at=datetime.now(UTC),
            )
            unit.snapshots.add(
                RunSnapshotRecord(
                    run_id=self._run_id,
                    event_sequence=event.sequence,
                    state=state.model_dump(mode="json"),
                    schema_version=self._schema_version,
                )
            )
            await unit.commit()

    async def load_latest(self) -> LoopState | None:
        async with UnitOfWork(self._session_factory) as unit:
            snapshot = await unit.snapshots.latest(self._run_id)
            if snapshot is None:
                return None
            if snapshot.schema_version != self._schema_version:
                raise SnapshotCompatibilityError(
                    "snapshot schema version does not match current runtime"
                )
            try:
                return LoopState.model_validate(snapshot.state)
            except ValidationError as error:
                raise SnapshotCompatibilityError("snapshot state is invalid") from error
