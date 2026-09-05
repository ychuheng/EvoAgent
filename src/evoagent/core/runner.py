"""管理一次 Agent Run 的完整生命周期。"""

import asyncio
from collections.abc import Iterable
from uuid import uuid4

from evoagent.config import Settings
from evoagent.core.context import ContextBuilder
from evoagent.core.events import InMemoryEventSink
from evoagent.core.loop import AgentLoop
from evoagent.core.models import (
    AgentLoopStatus,
    EventType,
    RunResult,
    RunStatus,
)
from evoagent.providers.base import ModelProvider
from evoagent.tools.executor import ToolExecutor
from evoagent.tools.registry import ToolRegistry


class AgentRunner:
    """为每次任务创建独立组件，并汇总唯一的 Run 终态。"""

    def __init__(
        self,
        settings: Settings,
        context_builder: ContextBuilder,
        provider: ModelProvider,
        registry: ToolRegistry,
    ) -> None:
        self._settings = settings
        self._context_builder = context_builder
        self._provider = provider
        self._registry = registry

    async def run(self, user_input: str, *, external_context: Iterable[str] = ()) -> RunResult:
        """运行一个任务，并把完成、失败、限制、超时或取消汇总为 RunResult。"""

        run_id = uuid4()
        sink = InMemoryEventSink(run_id)
        model = self._settings.model or "mock-model"
        await sink.emit(
            EventType.RUN_STARTED,
            {"provider": self._settings.provider.value, "model": model},
        )

        try:
            initial_messages = self._context_builder.build(
                user_input, external_context=external_context
            )
        except ValueError as error:
            await sink.emit(
                EventType.RUN_FAILED,
                {"error_code": "invalid_input", "error_message": str(error)},
            )
            return RunResult(
                run_id=run_id,
                status=RunStatus.FAILED,
                usage=None,
                events=sink.events,
                error_code="invalid_input",
                error_message=str(error),
            )

        executor = ToolExecutor(
            self._registry,
            sink,
            timeout_seconds=self._settings.tool_timeout_seconds,
            max_result_chars=self._settings.max_tool_result_chars,
        )
        loop = AgentLoop(
            self._provider,
            self._registry,
            executor,
            sink,
            model=model,
            max_iterations=self._settings.max_iterations,
            max_total_tokens=self._settings.max_total_tokens,
            max_repeated_tool_calls=self._settings.max_repeated_tool_calls,
        )

        try:
            async with asyncio.timeout(self._settings.task_timeout_seconds):
                loop_result = await loop.run(initial_messages)
        except asyncio.CancelledError:
            await sink.emit(
                EventType.RUN_CANCELLED,
                {"error_code": "run_cancelled", "error_message": "run was cancelled"},
            )
            return RunResult(
                run_id=run_id,
                status=RunStatus.CANCELLED,
                usage=None,
                events=sink.events,
                error_code="run_cancelled",
                error_message="run was cancelled",
            )
        except TimeoutError:
            message = f"run exceeded {self._settings.task_timeout_seconds:g} seconds"
            await sink.emit(
                EventType.RUN_TIMEOUT,
                {"error_code": "run_timeout", "error_message": message},
            )
            return RunResult(
                run_id=run_id,
                status=RunStatus.TIMEOUT,
                usage=None,
                events=sink.events,
                error_code="run_timeout",
                error_message=message,
            )
        except Exception as error:
            message = f"unhandled runtime error: {type(error).__name__}"
            await sink.emit(
                EventType.RUN_FAILED,
                {
                    "error_code": "internal_runtime_error",
                    "error_message": message,
                },
            )
            return RunResult(
                run_id=run_id,
                status=RunStatus.FAILED,
                usage=None,
                events=sink.events,
                error_code="internal_runtime_error",
                error_message=message,
            )

        if loop_result.status is AgentLoopStatus.COMPLETED:
            await sink.emit(
                EventType.RUN_COMPLETED,
                {
                    "iterations": loop_result.iterations,
                    "usage": (
                        loop_result.usage.model_dump(mode="json")
                        if loop_result.usage is not None
                        else None
                    ),
                },
            )
            return RunResult(
                run_id=run_id,
                status=RunStatus.COMPLETED,
                final_answer=loop_result.final_answer,
                usage=loop_result.usage,
                events=sink.events,
            )

        if loop_result.status is AgentLoopStatus.LIMIT_REACHED:
            terminal_event = EventType.RUN_LIMIT_REACHED
            run_status = RunStatus.LIMIT_REACHED
        else:
            terminal_event = EventType.RUN_FAILED
            run_status = RunStatus.FAILED
        await sink.emit(
            terminal_event,
            {
                "iterations": loop_result.iterations,
                "error_code": loop_result.error_code,
                "error_message": loop_result.error_message,
            },
        )
        return RunResult(
            run_id=run_id,
            status=run_status,
            usage=loop_result.usage,
            events=sink.events,
            error_code=loop_result.error_code,
            error_message=loop_result.error_message,
        )
