"""统一校验、执行工具并规范化工具结果。"""

import asyncio
from collections.abc import Iterable

from pydantic import ValidationError

from evoagent.core.events import RuntimeEventSink
from evoagent.core.models import EventType, ToolCall, ToolResult, ToolResultStatus
from evoagent.tools.base import ToolExecutionError
from evoagent.tools.registry import ToolNotFoundError, ToolRegistry


class ToolExecutor:
    """模型和具体工具之间唯一的执行入口。"""

    def __init__(
        self,
        registry: ToolRegistry,
        event_sink: RuntimeEventSink,
        *,
        timeout_seconds: float,
        max_result_chars: int,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if max_result_chars < 1:
            raise ValueError("max_result_chars must be positive")
        self._registry = registry
        self._event_sink = event_sink
        self._timeout_seconds = timeout_seconds
        self._max_result_chars = max_result_chars

    async def execute(self, call: ToolCall) -> ToolResult:
        """执行一次工具调用，并把可恢复失败转换成 ToolResult。"""

        await self._event_sink.emit(
            EventType.TOOL_STARTED,
            {
                "tool_call_id": call.call_id,
                "name": call.name,
                "arguments": dict(call.arguments),
            },
        )

        try:
            tool = self._registry.get(call.name)
        except ToolNotFoundError as error:
            return await self._failed_result(call, "tool_not_found", str(error))

        try:
            arguments = tool.validate_arguments(call.arguments)
        except ValidationError as error:
            return await self._failed_result(call, "invalid_arguments", str(error))

        try:
            content = await asyncio.wait_for(tool.invoke(arguments), timeout=self._timeout_seconds)
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            message = f"tool execution exceeded {self._timeout_seconds:g} seconds"
            return await self._failed_result(call, "tool_timeout", message)
        except ToolExecutionError as error:
            return await self._failed_result(call, "tool_execution_error", str(error))
        except Exception as error:
            await self._event_sink.emit(
                EventType.TOOL_FAILED,
                {
                    "tool_call_id": call.call_id,
                    "name": call.name,
                    "error_code": "internal_tool_error",
                    "error_type": type(error).__name__,
                },
            )
            raise

        if not isinstance(content, str):
            await self._event_sink.emit(
                EventType.TOOL_FAILED,
                {
                    "tool_call_id": call.call_id,
                    "name": call.name,
                    "error_code": "invalid_tool_result",
                    "actual_type": type(content).__name__,
                },
            )
            raise TypeError(f"tool {call.name} returned a non-string result")

        normalized, truncated = self._truncate(content)
        result = ToolResult(
            tool_call_id=call.call_id,
            name=call.name,
            status=ToolResultStatus.SUCCESS,
            content=normalized,
        )
        await self._event_sink.emit(
            EventType.TOOL_COMPLETED,
            {
                "tool_call_id": call.call_id,
                "name": call.name,
                "status": result.status.value,
                "content_chars": len(result.content),
                "truncated": truncated,
            },
        )
        return result

    async def execute_many(self, calls: Iterable[ToolCall]) -> tuple[ToolResult, ...]:
        """按模型给出的原始顺序执行并返回一批工具调用。"""

        results: list[ToolResult] = []
        for call in calls:
            results.append(await self.execute(call))
        return tuple(results)

    async def _failed_result(self, call: ToolCall, error_code: str, message: str) -> ToolResult:
        content, truncated = self._truncate(message)
        result = ToolResult(
            tool_call_id=call.call_id,
            name=call.name,
            status=ToolResultStatus.ERROR,
            content=content,
            error_code=error_code,
        )
        await self._event_sink.emit(
            EventType.TOOL_FAILED,
            {
                "tool_call_id": call.call_id,
                "name": call.name,
                "error_code": error_code,
                "error_message": content,
                "truncated": truncated,
            },
        )
        return result

    def _truncate(self, content: str) -> tuple[str, bool]:
        if len(content) <= self._max_result_chars:
            return content, False

        marker = "…[结果已截断]"
        if len(marker) >= self._max_result_chars:
            return content[: self._max_result_chars], True
        keep_chars = self._max_result_chars - len(marker)
        return f"{content[:keep_chars]}{marker}", True
