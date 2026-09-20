"""版本化 LoopState 快照的保存与加载。"""

from datetime import UTC, datetime
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from evoagent.core.models import LoopState
from evoagent.db.models import ContextRevisionRecord, RunSnapshotRecord
from evoagent.db.unit_of_work import UnitOfWork
from evoagent.memory.repository import check_run_references
from evoagent.tasks.lease_guard import LeaseGuard


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
        lease_guard: LeaseGuard | None = None,
    ) -> None:
        if schema_version < 1:
            raise ValueError("snapshot schema version must be positive")
        if lease_guard is not None and lease_guard.lease.run_id != run_id:
            raise ValueError("run does not match lease")
        self._lease_guard = lease_guard
        self._run_id = run_id
        self._session_factory = session_factory
        self._schema_version = schema_version

    async def save(self, state: LoopState) -> None:
        async with UnitOfWork(self._session_factory) as unit:
            if self._lease_guard is not None:
                await self._lease_guard.check(unit.session)
                await check_run_references(unit.session, self._run_id)
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
                state = LoopState.model_validate(snapshot.state)
                if state.schema_version != snapshot.schema_version:
                    raise SnapshotCompatibilityError("snapshot body version mismatch")
                if state.context_revision_id:
                    revision = await unit.session.get(
                        ContextRevisionRecord, state.context_revision_id
                    )
                    if (
                        revision is None
                        or revision.run_id != self._run_id
                        or revision.summary.get("erased")
                    ):
                        raise SnapshotCompatibilityError("context revision missing or erased")
                return state
            except ValidationError as error:
                raise SnapshotCompatibilityError("snapshot state is invalid") from error
