"""统一校验、执行工具并规范化工具结果。"""

import asyncio
from collections.abc import Iterable

from pydantic import ValidationError

from evoagent.core.events import RuntimeEventSink
from evoagent.core.models import EventType, ToolCall, ToolResult, ToolResultStatus, ToolViewMetadata
from evoagent.privacy.redaction import redact_text_result
from evoagent.tools.base import ToolArgumentValidationError, ToolExecutionError, ToolPermissionError
from evoagent.tools.execution import ToolExecutionMiddleware
from evoagent.tools.output_view import VIEW_HEADER_RESERVE
from evoagent.tools.registry import ToolNotFoundError, ToolRegistry

#: 回读归档的工具：其正文在写入时已脱敏，无法证明是原文，投影时标 unknown。
ARCHIVE_READ_TOOLS = frozenset({"artifact_read"})


class ToolExecutor:
    """模型和具体工具之间唯一的执行入口。"""

    def __init__(
        self,
        registry: ToolRegistry,
        event_sink: RuntimeEventSink,
        *,
        timeout_seconds: float,
        max_result_chars: int,
        middleware: ToolExecutionMiddleware | None = None,
        output_store=None,
        service_gate=None,
        lease_check=None,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if max_result_chars < 1:
            raise ValueError("max_result_chars must be positive")
        self._registry = registry
        self._event_sink = event_sink
        self._timeout_seconds = timeout_seconds
        self._max_result_chars = max_result_chars
        self._middleware = middleware
        self._output_store = output_store
        self._service_gate = service_gate
        self._lease_check = lease_check

    async def execute(self, call: ToolCall) -> ToolResult:
        """执行一次工具调用，并把可恢复失败转换成 ToolResult。"""
        from evoagent.workers.rate_limit import RateLimited

        try:
            tool = self._registry.get(call.name)
        except ToolNotFoundError:
            return await self._execute(call)
        binding = getattr(tool, "binding", {})
        if self._service_gate is not None and binding.get("server_id"):
            try:
                async with self._service_gate.acquire(
                    f"mcp:{binding['server_id']}", self._lease_check
                ):
                    return await self._execute(call)
            except RateLimited:
                return await self._failed_result(call, "rate_limited", "MCP quota unavailable")
        if self._lease_check is not None:
            await self._lease_check()
        return await self._execute(call)

    async def _execute(self, call: ToolCall) -> ToolResult:
        """所有返回路径的统一出口：先执行，再做安全输出投影。"""

        result = await self._execute_inner(call)
        return self._project(call, result)

    def _project(self, call: ToolCall, result: ToolResult) -> ToolResult:
        """安全输出投影：保证每条返回路径都带视图元数据。

        成功路径已由 `ToolOutputStore.preserve()` 投影（带 `view_metadata`），直接放行；
        失败信息与**复用/旧缓存**结果在这里补做当前策略检查——只改 preserve 会漏掉
        这些提前返回的分支（改造方案 §2.3）。
        """

        if result.view_metadata is not None:
            return result
        checked = redact_text_result(result.content)
        content, truncated = self._truncate(checked.text)
        source_view = (
            "redacted"
            if checked.changed
            else "unknown"
            if call.name in ARCHIVE_READ_TOOLS
            else "verbatim"
        )
        return result.model_copy(
            update={
                "content": content,
                "view_metadata": ToolViewMetadata(
                    redacted=checked.changed,
                    rule_categories=checked.categories,
                    policy_version=checked.policy_version,
                    truncated=truncated,
                    source_view=source_view,
                ),
            }
        )

    async def _execute_inner(self, call: ToolCall) -> ToolResult:

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
        except (ValidationError, ToolArgumentValidationError) as error:
            return await self._failed_result(call, "invalid_arguments", str(error))

        if getattr(tool, "requires_persistent_execution", False) and self._middleware is None:
            return await self._failed_result(
                call, "mcp_persistence_required", "mcp_persistence_required"
            )
        preflight = getattr(tool, "preflight", None)
        if preflight is not None:
            try:
                await preflight()
            except ToolExecutionError as error:
                return await self._failed_result(call, error.code, str(error))
        token = None
        if self._middleware is not None:
            directive = await self._middleware.before(call, tool, arguments)
            token = directive.token
            if directive.result is not None:
                await self._emit_existing_result(directive.result)
                return directive.result

        try:
            invoke_with_evidence = getattr(tool, "invoke_with_evidence", None)
            evidence: dict[str, object] | None = None
            if invoke_with_evidence is not None:
                content, evidence = await asyncio.wait_for(
                    invoke_with_evidence(arguments), timeout=self._timeout_seconds
                )
            else:
                content = await asyncio.wait_for(
                    tool.invoke(arguments), timeout=self._timeout_seconds
                )
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            message = f"tool execution exceeded {self._timeout_seconds:g} seconds"
            if self._middleware is not None:
                await self._middleware.after_failure(token, "tool_timeout", message)
            return await self._failed_result(call, "tool_timeout", message)
        except ToolPermissionError as error:
            if self._middleware is not None:
                await self._middleware.after_failure(token, "permission_denied", str(error))
            return await self._failed_result(
                call,
                "permission_denied",
                str(error),
                status=ToolResultStatus.PERMISSION_DENIED,
            )
        except ToolExecutionError as error:
            if self._middleware is not None:
                await self._middleware.after_failure(token, error.code, str(error))
            return await self._failed_result(call, error.code, str(error))
        except Exception as error:
            if self._middleware is not None:
                await self._middleware.after_failure(
                    token, "internal_tool_error", type(error).__name__
                )
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

        original_size = len(content)
        prepared = None
        if self._output_store is not None:
            # 头部要能放进模型可见的预算里，所以正文按"预算 − 头部预留"截断
            body_limit = max(1, self._max_result_chars - VIEW_HEADER_RESERVE)
            prepared = await self._output_store.preserve(content, body_limit)
            content = prepared.content
        normalized, truncated = self._truncate(content)
        truncated = (
            truncated
            or original_size > self._max_result_chars
            or (prepared is not None and prepared.view_metadata.truncated)
        )
        if self._middleware is not None:
            await self._middleware.after_success(token, normalized)
        if evidence is not None:
            await self._event_sink.emit(
                EventType.SOURCE_OBSERVED,
                {"tool_call_id": call.call_id, **evidence},
            )
        result = ToolResult(
            tool_call_id=call.call_id,
            name=call.name,
            status=ToolResultStatus.SUCCESS,
            content=normalized,
            view_metadata=prepared.view_metadata if prepared is not None else None,
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

    async def _emit_existing_result(self, result: ToolResult) -> None:
        event_type = (
            EventType.TOOL_COMPLETED
            if result.status is ToolResultStatus.SUCCESS
            else EventType.TOOL_FAILED
        )
        await self._event_sink.emit(
            event_type,
            {
                "tool_call_id": result.tool_call_id,
                "name": result.name,
                "status": result.status.value,
                "error_code": result.error_code,
                "reused": True,
            },
        )

    async def execute_many(self, calls: Iterable[ToolCall]) -> tuple[ToolResult, ...]:
        """安全调用可以并发执行，其他调用按原始顺序执行。"""

        call_batch = tuple(calls)
        if self._can_run_in_parallel(call_batch):
            return tuple(await asyncio.gather(*(self.execute(call) for call in call_batch)))
        results: list[ToolResult] = []
        for call in call_batch:
            results.append(await self.execute(call))
        return tuple(results)

    async def _failed_result(
        self,
        call: ToolCall,
        error_code: str,
        message: str,
        *,
        status: ToolResultStatus = ToolResultStatus.ERROR,
    ) -> ToolResult:
        content, truncated = self._truncate(message)
        result = ToolResult(
            tool_call_id=call.call_id,
            name=call.name,
            status=status,
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

    def _can_run_in_parallel(self, calls: tuple[ToolCall, ...]) -> bool:
        if self._middleware is not None:
            return False
        if len(calls) < 2:
            return False
        try:
            tools = tuple(self._registry.get(call.name) for call in calls)
        except ToolNotFoundError:
            return False
        return all(not tool.has_side_effects and tool.parallel_safe for tool in tools)
