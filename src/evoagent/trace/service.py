"""将持久化记录组装成稳定的 Run Trace。"""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from evoagent.db.models import (
    ArtifactRecord,
    RunSnapshotRecord,
    ToolApprovalRecord,
    ToolCallRecord,
    ToolEffectRecord,
    TurnRecord,
)
from evoagent.db.unit_of_work import UnitOfWork
from evoagent.tasks.state_machine import PersistentRunStatus


class TraceEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    sequence: int
    event_type: str
    payload: dict[str, Any]
    schema_version: int
    created_at: datetime


class RunTrace(BaseModel):
    model_config = ConfigDict(frozen=True)

    run_id: UUID
    task_id: UUID
    status: PersistentRunStatus
    final_answer: str | None
    error_code: str | None
    events: tuple[TraceEvent, ...]
    turns: tuple[dict[str, Any], ...]
    tool_calls: tuple[dict[str, Any], ...]
    tool_effects: tuple[dict[str, Any], ...]
    approvals: tuple[dict[str, Any], ...]
    snapshots: tuple[dict[str, Any], ...]
    artifacts: tuple[dict[str, Any], ...]


class TraceService:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def get_run_trace(self, run_id: UUID) -> RunTrace:
        """查询 Run 当前状态和按 sequence 排序的完整事件。"""

        async with UnitOfWork(self._session_factory) as unit:
            run = await unit.runs.get(run_id)
            records = await unit.events.list_for_run(run_id)
            turns = tuple(
                await unit.session.scalars(
                    select(TurnRecord)
                    .where(TurnRecord.run_id == run_id)
                    .order_by(TurnRecord.sequence)
                )
            )
            calls = tuple(
                await unit.session.scalars(
                    select(ToolCallRecord)
                    .where(ToolCallRecord.run_id == run_id)
                    .order_by(ToolCallRecord.created_at)
                )
            )
            call_ids = [call.id for call in calls]
            effects = (
                tuple(
                    await unit.session.scalars(
                        select(ToolEffectRecord).where(ToolEffectRecord.tool_call_id.in_(call_ids))
                    )
                )
                if call_ids
                else ()
            )
            approvals = (
                tuple(
                    await unit.session.scalars(
                        select(ToolApprovalRecord).where(
                            ToolApprovalRecord.tool_call_id.in_(call_ids)
                        )
                    )
                )
                if call_ids
                else ()
            )
            snapshots = tuple(
                await unit.session.scalars(
                    select(RunSnapshotRecord)
                    .where(RunSnapshotRecord.run_id == run_id)
                    .order_by(RunSnapshotRecord.event_sequence)
                )
            )
            artifacts = tuple(
                await unit.session.scalars(
                    select(ArtifactRecord)
                    .where(ArtifactRecord.run_id == run_id)
                    .order_by(ArtifactRecord.created_at)
                )
            )
        return RunTrace(
            run_id=run.id,
            task_id=run.task_id,
            status=run.status,
            final_answer=run.final_answer,
            error_code=run.error_code,
            events=tuple(
                TraceEvent(
                    sequence=record.sequence,
                    event_type=record.event_type,
                    payload=record.payload,
                    schema_version=record.schema_version,
                    created_at=record.created_at,
                )
                for record in records
            ),
            turns=tuple(
                {
                    "id": str(item.id),
                    "sequence": item.sequence,
                    "status": item.status,
                    "request_summary": item.request_summary,
                    "response_summary": item.response_summary,
                    "usage": item.usage,
                }
                for item in turns
            ),
            tool_calls=tuple(
                {
                    "id": str(item.id),
                    "provider_call_id": item.provider_call_id,
                    "tool_name": item.tool_name,
                    "arguments": item.arguments,
                    "risk": item.risk,
                    "status": item.status.value,
                    "result_summary": item.result_summary,
                    "error_code": item.error_code,
                }
                for item in calls
            ),
            tool_effects=tuple(
                {
                    "id": str(item.id),
                    "tool_call_id": str(item.tool_call_id),
                    "status": item.status.value,
                    "semantic_key": item.semantic_key,
                    "result_hash": item.result_hash,
                }
                for item in effects
            ),
            approvals=tuple(
                {
                    "id": str(item.id),
                    "tool_call_id": str(item.tool_call_id),
                    "status": item.status.value,
                    "risk": item.risk,
                    "reason": item.reason,
                    "response": item.response,
                }
                for item in approvals
            ),
            snapshots=tuple(
                {
                    "id": str(item.id),
                    "event_sequence": item.event_sequence,
                    "schema_version": item.schema_version,
                    "context_ref": item.context_ref,
                }
                for item in snapshots
            ),
            artifacts=tuple(
                {
                    "id": str(item.id),
                    "type": item.type,
                    "uri": item.uri,
                    "content_hash": item.content_hash,
                    "size_bytes": item.size_bytes,
                    "metadata": item.attributes,
                }
                for item in artifacts
            ),
        )
