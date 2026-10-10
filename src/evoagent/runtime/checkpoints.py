"""版本化 LoopState 快照的保存与加载。"""

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from evoagent.core.context_policy import ContextPolicyError, LegacyContextPolicy
from evoagent.core.models import LoopState
from evoagent.db.models import ContextRevisionRecord, RunSnapshotRecord
from evoagent.db.unit_of_work import UnitOfWork
from evoagent.memory.repository import check_run_references
from evoagent.runtime.context_store import ContextStore
from evoagent.skills.canonical import content_hash
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
        settings=None,
        lease_guard: LeaseGuard | None = None,
    ) -> None:
        if schema_version < 1:
            raise ValueError("snapshot schema version must be positive")
        if lease_guard is not None and lease_guard.lease.run_id != run_id:
            raise ValueError("run does not match lease")
        self._settings = settings
        self._lease_guard = lease_guard
        self._run_id = run_id
        self._session_factory = session_factory
        self._schema_version = schema_version
        self._committed_digest: str | None = None
        self._save_lock = asyncio.Lock()

    async def save(self, state: LoopState) -> None:
        await self._save(
            state,
            skip_unchanged=bool(
                getattr(self._settings, "runtime_snapshot_deduplication_enabled", False)
            ),
        )

    async def save_if_changed(self, state: LoopState) -> None:
        await self._save(state, skip_unchanged=True)

    async def _save(self, state: LoopState, *, skip_unchanged: bool) -> None:
        body = state.model_dump(
            mode="json", exclude_none=False, exclude_unset=False, exclude_defaults=False
        )
        digest = content_hash({"schema_version": self._schema_version, "state": body})
        async with self._save_lock:
            try:
                await self._persist(body, state, digest, skip_unchanged=skip_unchanged)
            except BaseException:
                # A commit exception may mean the commit succeeded remotely. Do
                # not use the previous cache entry after any ambiguous outcome.
                self._committed_digest = None
                raise

    async def _persist(self, body, state, digest, *, skip_unchanged):
        async with UnitOfWork(self._session_factory) as unit:
            if self._lease_guard is not None:
                await self._lease_guard.check(unit.session)
                await check_run_references(unit.session, self._run_id)
            if skip_unchanged and digest == self._committed_digest:
                # Cache only saves writes; authority still comes from this
                # transaction. A recovered worker starts with an empty cache.
                await unit.commit()
                return
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
                    state=body,
                    schema_version=self._schema_version,
                )
            )
            await unit.commit()
        self._committed_digest = digest

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
                # Schema-v1 does not otherwise install ContextStore. Check before
                # pending resumed tools can execute, as well as before model dispatch.
                guard = self._lease_guard or SimpleNamespace(
                    lease=SimpleNamespace(run_id=self._run_id)
                )
                context = ContextStore(
                    self._session_factory,
                    guard,
                    None,
                    LegacyContextPolicy(),
                    settings=self._settings,
                )
                try:
                    await context.verify_restored_messages(state)
                except ContextPolicyError as error:
                    raise SnapshotCompatibilityError(
                        "restored derived messages require new safe context"
                    ) from error
                return state
            except ValidationError as error:
                raise SnapshotCompatibilityError("snapshot state is invalid") from error
