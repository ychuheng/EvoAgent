"""租约过期后的可审计恢复决策。"""

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from evoagent.db.models import (
    ApprovalStatus,
    RunRecord,
    TaskRecord,
    ToolApprovalRecord,
    ToolCallRecord,
    ToolEffectRecord,
    ToolEffectStatus,
)
from evoagent.db.unit_of_work import UnitOfWork
from evoagent.runtime.checkpoints import (
    PersistentCheckpointStore,
    SnapshotCompatibilityError,
)
from evoagent.tasks.state_machine import (
    PersistentRunStatus,
    TaskStatus,
    ensure_run_transition,
    ensure_task_transition,
)


class RecoveryAction(StrEnum):
    RESTART = "restart"
    RESUME = "resume"
    FAIL_UNSAFE = "fail_unsafe"
    FAIL_INCOMPATIBLE = "fail_incompatible"
    WAITING_CONFIRMATION = "waiting_confirmation"


@dataclass(frozen=True, slots=True)
class RecoveryDecision:
    action: RecoveryAction
    snapshot_event_sequence: int | None
    ignored_event_count: int


class RecoveryService:
    """检查快照与未决副作用，再决定从头重跑、续跑或失败。"""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        snapshot_schema_version: int,
    ) -> None:
        self._session_factory = session_factory
        self._snapshot_schema_version = snapshot_schema_version

    async def recover(self, task_id: UUID) -> RecoveryDecision:
        async with UnitOfWork(self._session_factory) as unit:
            task = await unit.session.scalar(
                select(TaskRecord).where(TaskRecord.id == task_id).with_for_update()
            )
            if task is None:
                raise ValueError(f"task does not exist: {task_id}")
            run = await unit.session.scalar(
                select(RunRecord)
                .where(RunRecord.task_id == task_id)
                .order_by(RunRecord.created_at.desc())
                .with_for_update()
                .limit(1)
            )
            if run is None:
                raise ValueError(f"task has no run: {task_id}")
            if task.status is not TaskStatus.RECOVERING or (
                run.status is not PersistentRunStatus.RECOVERING
            ):
                raise ValueError("task and run must both be recovering")

            unsafe_effect = await unit.session.scalar(
                select(ToolEffectRecord)
                .join(ToolCallRecord, ToolCallRecord.id == ToolEffectRecord.tool_call_id)
                .where(
                    ToolCallRecord.run_id == run.id,
                    ToolEffectRecord.status.in_(
                        (
                            ToolEffectStatus.PREPARED,
                            ToolEffectStatus.EXECUTING,
                            ToolEffectStatus.UNKNOWN,
                        )
                    ),
                )
                .limit(1)
            )
            if unsafe_effect is not None:
                unsafe_effect.status = ToolEffectStatus.UNKNOWN
                approval = await unit.session.scalar(
                    select(ToolApprovalRecord).where(
                        ToolApprovalRecord.tool_call_id == unsafe_effect.tool_call_id
                    )
                )
                if approval is None:
                    call = await unit.session.get(ToolCallRecord, unsafe_effect.tool_call_id)
                    if call is None:
                        raise RuntimeError("tool effect has no tool call")
                    approval = ToolApprovalRecord(
                        task_id=task.id,
                        tool_call_id=call.id,
                        status=ApprovalStatus.PENDING,
                        risk=call.risk,
                        reason=(
                            "副作用结果未知；请用 response=retry 表示确认未提交，"
                            "或 response=committed:<结果> 表示确认已提交"
                        ),
                    )
                    unit.session.add(approval)
                    await unit.session.flush()
                ensure_task_transition(task.status, TaskStatus.WAITING_USER)
                ensure_run_transition(run.status, PersistentRunStatus.WAITING_USER)
                task.status = TaskStatus.WAITING_USER
                task.lock_version += 1
                run.status = PersistentRunStatus.WAITING_USER
                run.lock_version += 1
                await unit.events.append(
                    run_id=run.id,
                    event_type="recovery.decided",
                    payload={
                        "action": RecoveryAction.WAITING_CONFIRMATION.value,
                        "approval_id": str(approval.id),
                    },
                    created_at=datetime.now(UTC),
                )
                await unit.commit()
                return RecoveryDecision(
                    action=RecoveryAction.WAITING_CONFIRMATION,
                    snapshot_event_sequence=None,
                    ignored_event_count=0,
                )

            checkpoint = PersistentCheckpointStore(
                run.id,
                self._session_factory,
                schema_version=self._snapshot_schema_version,
            )
            try:
                state = await checkpoint.load_latest()
            except SnapshotCompatibilityError:
                return await self._fail(
                    unit,
                    task,
                    run,
                    action=RecoveryAction.FAIL_INCOMPATIBLE,
                    code="snapshot_incompatible",
                )
            snapshot = await unit.snapshots.latest(run.id)
            snapshot_sequence = snapshot.event_sequence if snapshot is not None else None
            events = await unit.events.list_for_run(
                run.id,
                after_sequence=snapshot_sequence or 0,
            )
            action = RecoveryAction.RESUME if state is not None else RecoveryAction.RESTART
            ensure_task_transition(task.status, TaskStatus.QUEUED)
            ensure_run_transition(run.status, PersistentRunStatus.QUEUED)
            task.status = TaskStatus.QUEUED
            task.next_attempt_at = datetime.now(UTC)
            task.lock_version += 1
            run.status = PersistentRunStatus.QUEUED
            run.lock_version += 1
            await unit.events.append(
                run_id=run.id,
                event_type="recovery.decided",
                payload={
                    "action": action.value,
                    "snapshot_event_sequence": snapshot_sequence,
                    "ignored_event_count": len(events),
                },
                created_at=datetime.now(UTC),
            )
            await unit.commit()
            return RecoveryDecision(
                action=action,
                snapshot_event_sequence=snapshot_sequence,
                ignored_event_count=len(events),
            )

    async def _fail(
        self,
        unit: UnitOfWork,
        task: TaskRecord,
        run: RunRecord,
        *,
        action: RecoveryAction,
        code: str,
    ) -> RecoveryDecision:
        ensure_task_transition(task.status, TaskStatus.FAILED)
        ensure_run_transition(run.status, PersistentRunStatus.FAILED)
        task.status = TaskStatus.FAILED
        task.lock_version += 1
        run.status = PersistentRunStatus.FAILED
        run.error_code = code
        run.error_message = "recovery stopped because automatic replay is unsafe"
        run.ended_at = datetime.now(UTC)
        run.lock_version += 1
        await unit.events.append(
            run_id=run.id,
            event_type="recovery.failed",
            payload={"action": action.value, "error_code": code},
            created_at=datetime.now(UTC),
        )
        await unit.commit()
        return RecoveryDecision(
            action=action,
            snapshot_event_sequence=None,
            ignored_event_count=0,
        )
