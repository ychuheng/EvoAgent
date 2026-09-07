"""持久化权限判断、工具调用 Trace 与副作用幂等账本。"""

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from evoagent.core.models import ToolCall, ToolResult, ToolResultStatus
from evoagent.db.models import (
    ApprovalStatus,
    ToolApprovalRecord,
    ToolCallRecord,
    ToolCallStatus,
    ToolEffectRecord,
    ToolEffectStatus,
    TurnRecord,
)
from evoagent.db.unit_of_work import UnitOfWork
from evoagent.tools.approvals import ApprovalRequiredError
from evoagent.tools.base import ToolInstance
from evoagent.tools.execution import ToolExecutionDirective
from evoagent.tools.policy import PermissionPolicy, PolicyAction


@dataclass(frozen=True, slots=True)
class EffectToken:
    tool_call_id: UUID
    effect_id: UUID | None


class PersistentToolMiddleware:
    """在工具实际运行前后维护 Policy、Approval 与 ToolEffect。"""

    def __init__(
        self,
        *,
        task_id: UUID,
        run_id: UUID,
        session_factory: async_sessionmaker[AsyncSession],
        policy: PermissionPolicy,
    ) -> None:
        self._task_id = task_id
        self._run_id = run_id
        self._session_factory = session_factory
        self._policy = policy

    async def before(
        self, call: ToolCall, tool: ToolInstance, arguments: BaseModel
    ) -> ToolExecutionDirective:
        decision = self._policy.evaluate(tool, arguments)
        async with UnitOfWork(self._session_factory) as unit:
            record = await self._get_or_create_call(unit, call, decision.effective_risk.value)
            approval = await unit.session.scalar(
                select(ToolApprovalRecord).where(ToolApprovalRecord.tool_call_id == record.id)
            )
            if approval is None:
                # Worker 重新排队后，Provider 可能为同一语义调用生成新的 call_id。
                # 审批决定因此按“任务 + 工具 + 参数”复用，call_id 只负责 Trace 关联。
                approval = await self._find_semantic_approval(
                    unit, tool.name, arguments.model_dump(mode="json")
                )
            if decision.action is PolicyAction.DENY or (
                approval is not None and approval.status is ApprovalStatus.REJECTED
            ):
                record.status = ToolCallStatus.DENIED
                await unit.commit()
                return ToolExecutionDirective(
                    result=ToolResult(
                        tool_call_id=call.call_id,
                        name=call.name,
                        status=ToolResultStatus.PERMISSION_DENIED,
                        content="tool execution was denied by permission policy",
                        error_code="permission_denied",
                    )
                )
            if (
                tool.name == "ask_user"
                and approval is not None
                and approval.status is ApprovalStatus.APPROVED
                and approval.response is not None
            ):
                record.status = ToolCallStatus.SUCCEEDED
                record.result_summary = approval.response
                await unit.commit()
                return ToolExecutionDirective(
                    result=ToolResult(
                        tool_call_id=call.call_id,
                        name=call.name,
                        status=ToolResultStatus.SUCCESS,
                        content=approval.response,
                    )
                )
            if decision.action is PolicyAction.REQUIRE_APPROVAL and (
                approval is None or approval.status is ApprovalStatus.PENDING
            ):
                if approval is None:
                    approval = ToolApprovalRecord(
                        task_id=self._task_id,
                        tool_call_id=record.id,
                        risk=decision.effective_risk.value,
                        reason=(
                            str(arguments.model_dump().get("question", decision.reason))
                            if tool.name == "ask_user"
                            else decision.reason
                        ),
                    )
                    unit.session.add(approval)
                    await unit.session.flush()
                await unit.events.append(
                    run_id=self._run_id,
                    event_type="approval.required",
                    payload={"approval_id": str(approval.id), "tool": tool.name},
                    created_at=datetime.now(UTC),
                )
                await unit.commit()
                raise ApprovalRequiredError(approval.id, decision.reason)

            effect_id = None
            if tool.has_side_effects:
                semantic_key = self.semantic_key(tool.name, arguments.model_dump(mode="json"))
                effect = await unit.effects.find(str(self._task_id), semantic_key)
                if effect is not None and effect.status is ToolEffectStatus.COMMITTED:
                    content = effect.result_content or "副作用已提交"
                    record.status = ToolCallStatus.SUCCEEDED
                    record.result_summary = content
                    await unit.commit()
                    return ToolExecutionDirective(
                        result=ToolResult(
                            tool_call_id=call.call_id,
                            name=call.name,
                            status=ToolResultStatus.SUCCESS,
                            content=content,
                        )
                    )
                if effect is not None and effect.status in (
                    ToolEffectStatus.EXECUTING,
                    ToolEffectStatus.UNKNOWN,
                ):
                    effect.status = ToolEffectStatus.UNKNOWN
                    if (
                        approval is not None
                        and approval.status is ApprovalStatus.APPROVED
                        and approval.response == "retry"
                    ):
                        effect.status = ToolEffectStatus.EXECUTING
                        record.status = ToolCallStatus.RUNNING
                        await unit.commit()
                        return ToolExecutionDirective(token=EffectToken(record.id, effect.id))
                    if (
                        approval is not None
                        and approval.status is ApprovalStatus.APPROVED
                        and approval.response is not None
                        and approval.response.startswith("committed:")
                    ):
                        content = approval.response.removeprefix("committed:")
                        effect.status = ToolEffectStatus.COMMITTED
                        effect.result_content = content
                        effect.result_hash = (
                            "sha256:" + hashlib.sha256(content.encode()).hexdigest()
                        )
                        effect.committed_at = datetime.now(UTC)
                        record.status = ToolCallStatus.SUCCEEDED
                        record.result_summary = content
                        await unit.commit()
                        return ToolExecutionDirective(
                            result=ToolResult(
                                tool_call_id=call.call_id,
                                name=call.name,
                                status=ToolResultStatus.SUCCESS,
                                content=content,
                            )
                        )
                    if approval is None:
                        approval = ToolApprovalRecord(
                            task_id=self._task_id,
                            tool_call_id=record.id,
                            risk=decision.effective_risk.value,
                            reason="side effect outcome is unknown",
                        )
                        unit.session.add(approval)
                        await unit.session.flush()
                    await unit.commit()
                    raise ApprovalRequiredError(approval.id, "side effect outcome is unknown")
                if effect is None:
                    effect = ToolEffectRecord(
                        tool_call_id=record.id,
                        effect_scope=str(self._task_id),
                        semantic_key=semantic_key,
                    )
                    unit.effects.add(effect)
                    await unit.session.flush()
                effect.status = ToolEffectStatus.EXECUTING
                effect_id = effect.id
            record.status = ToolCallStatus.RUNNING
            await unit.commit()
            return ToolExecutionDirective(token=EffectToken(record.id, effect_id))

    async def after_success(self, token: Any, content: str) -> None:
        if not isinstance(token, EffectToken):
            return
        async with UnitOfWork(self._session_factory) as unit:
            call = await unit.session.get(ToolCallRecord, token.tool_call_id)
            if call is not None:
                call.status = ToolCallStatus.SUCCEEDED
                call.result_summary = content
            if token.effect_id is not None:
                effect = await unit.session.get(ToolEffectRecord, token.effect_id)
                if effect is not None:
                    effect.status = ToolEffectStatus.COMMITTED
                    effect.result_content = content
                    effect.result_hash = "sha256:" + hashlib.sha256(content.encode()).hexdigest()
                    effect.committed_at = datetime.now(UTC)
            await unit.commit()

    async def after_failure(self, token: Any, error_code: str, message: str) -> None:
        if not isinstance(token, EffectToken):
            return
        async with UnitOfWork(self._session_factory) as unit:
            call = await unit.session.get(ToolCallRecord, token.tool_call_id)
            if call is not None:
                call.status = ToolCallStatus.FAILED
                call.error_code = error_code
                call.result_summary = message
            if token.effect_id is not None:
                effect = await unit.session.get(ToolEffectRecord, token.effect_id)
                if effect is not None:
                    effect.status = ToolEffectStatus.UNKNOWN
            await unit.commit()

    async def _get_or_create_call(
        self, unit: UnitOfWork, call: ToolCall, risk: str
    ) -> ToolCallRecord:
        existing = await unit.session.scalar(
            select(ToolCallRecord).where(
                ToolCallRecord.run_id == self._run_id,
                ToolCallRecord.provider_call_id == call.call_id,
            )
        )
        if existing is not None:
            return existing
        next_sequence = (
            await unit.session.scalar(
                select(func.coalesce(func.max(TurnRecord.sequence), 0)).where(
                    TurnRecord.run_id == self._run_id
                )
            )
        ) + 1
        turn = TurnRecord(run_id=self._run_id, sequence=next_sequence, status="running")
        unit.session.add(turn)
        await unit.session.flush()
        record = ToolCallRecord(
            run_id=self._run_id,
            turn_id=turn.id,
            provider_call_id=call.call_id,
            tool_name=call.name,
            arguments=dict(call.arguments),
            risk=risk,
        )
        unit.session.add(record)
        await unit.session.flush()
        return record

    async def _find_semantic_approval(
        self,
        unit: UnitOfWork,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> ToolApprovalRecord | None:
        rows = await unit.session.execute(
            select(ToolApprovalRecord, ToolCallRecord)
            .join(ToolCallRecord, ToolCallRecord.id == ToolApprovalRecord.tool_call_id)
            .where(
                ToolApprovalRecord.task_id == self._task_id,
                ToolCallRecord.tool_name == tool_name,
            )
            .order_by(ToolApprovalRecord.requested_at.desc())
        )
        for approval, call in rows:
            if call.arguments == arguments:
                return approval
        return None

    @staticmethod
    def semantic_key(tool_name: str, arguments: dict[str, Any]) -> str:
        value = json.dumps(
            {"tool": tool_name, "arguments": arguments},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(value.encode()).hexdigest()
