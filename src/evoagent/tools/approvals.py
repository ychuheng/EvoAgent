"""工具审批的创建、决策与任务恢复。"""

from datetime import UTC, datetime
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
from evoagent.mcp.bindings import check_binding
from evoagent.mcp.schema import MCPError
from evoagent.tasks.state_machine import PersistentRunStatus, TaskStatus


class ApprovalRequiredError(RuntimeError):
    def __init__(self, approval_id: UUID, message: str) -> None:
        super().__init__(message)
        self.approval_id = approval_id


class ApprovalServiceError(ValueError):
    code = "approval_error"


class ApprovalService:
    """记录用户决定，并把 WAITING_USER 任务重新放回队列。"""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def decide(
        self,
        approval_id: UUID,
        *,
        approved: bool,
        response: str | None = None,
    ) -> ToolApprovalRecord:
        async with UnitOfWork(self._session_factory) as unit:
            reference = await unit.session.get(ToolApprovalRecord, approval_id)
            if reference is None:
                raise ApprovalServiceError(f"approval does not exist: {approval_id}")
            task = await unit.session.scalar(
                select(TaskRecord).where(TaskRecord.id == reference.task_id).with_for_update()
            )
            run = await unit.session.scalar(
                select(RunRecord)
                .where(RunRecord.task_id == reference.task_id)
                .order_by(RunRecord.created_at.desc())
                .with_for_update()
                .limit(1)
            )
            if task is None or run is None:
                raise ApprovalServiceError("approval task or run does not exist")
            approval = await unit.session.scalar(
                select(ToolApprovalRecord)
                .where(ToolApprovalRecord.id == approval_id)
                .execution_options(populate_existing=True)
                .with_for_update()
            )
            if approval is None:
                raise ApprovalServiceError(f"approval does not exist: {approval_id}")
            if approval.status is not ApprovalStatus.PENDING:
                raise ApprovalServiceError("approval has already been decided")
            if task.status in {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED} or (
                run.status
                in {
                    PersistentRunStatus.COMPLETED,
                    PersistentRunStatus.FAILED,
                    PersistentRunStatus.CANCELLED,
                    PersistentRunStatus.TIMEOUT,
                    PersistentRunStatus.LIMIT_REACHED,
                }
            ):
                raise ApprovalServiceError("approval task is terminal")
            call = await unit.session.get(ToolCallRecord, approval.tool_call_id)
            if call is None:
                raise ApprovalServiceError("approval tool call does not exist")
            if call.execution_binding and "server_id" in call.execution_binding:
                try:
                    await check_binding(unit.session, call.execution_binding, lock=True)
                except MCPError:
                    raise ApprovalServiceError("tool_manifest_changed") from None
            normalized_response = response.strip() if response and response.strip() else None
            if approved and call.tool_name == "ask_user" and normalized_response is None:
                raise ApprovalServiceError("ask_user approval requires a response")
            effect = await unit.session.scalar(
                select(ToolEffectRecord).where(
                    ToolEffectRecord.tool_call_id == approval.tool_call_id
                )
            )
            if (
                approved
                and effect is not None
                and effect.status is ToolEffectStatus.UNKNOWN
                and normalized_response != "retry"
                and not (normalized_response or "").startswith("committed:")
            ):
                raise ApprovalServiceError(
                    "unknown effect response must be retry or committed:<result>"
                )
            approval.status = ApprovalStatus.APPROVED if approved else ApprovalStatus.REJECTED
            approval.response = normalized_response
            approval.decided_at = datetime.now(UTC)
            await unit.session.flush()
            pending = await unit.session.scalar(
                select(ToolApprovalRecord.id)
                .where(
                    ToolApprovalRecord.task_id == task.id,
                    ToolApprovalRecord.status == ApprovalStatus.PENDING,
                )
                .limit(1)
            )
            if pending is None and task.status is TaskStatus.WAITING_USER:
                task.status = TaskStatus.QUEUED
                task.lock_version += 1
            if pending is None and run.status is PersistentRunStatus.WAITING_USER:
                run.status = PersistentRunStatus.QUEUED
                run.lock_version += 1
            await unit.events.append(
                run_id=run.id,
                event_type="approval.decided",
                payload={
                    "approval_id": str(approval.id),
                    "decision": approval.status.value,
                    "has_response": approval.response is not None,
                },
                created_at=datetime.now(UTC),
            )
            await unit.commit()
            return approval

    async def get(self, approval_id: UUID) -> ToolApprovalRecord:
        async with self._session_factory() as session:
            approval = await session.get(ToolApprovalRecord, approval_id)
            if approval is None:
                raise ApprovalServiceError(f"approval does not exist: {approval_id}")
            return approval
