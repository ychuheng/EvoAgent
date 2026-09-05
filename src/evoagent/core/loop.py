"""控制模型请求和工具执行之间的多轮循环。"""

import asyncio
import json

from evoagent.core.events import RuntimeEventSink
from evoagent.core.models import (
    AgentLoopResult,
    AgentLoopStatus,
    EventType,
    FinishReason,
    Message,
    MessageRole,
    ModelRequest,
    ModelResponse,
    ProviderEventType,
    TokenUsage,
    ToolCall,
    ToolResult,
    ToolResultStatus,
)
from evoagent.providers.base import ModelProvider, ProviderError, ProviderProtocolError
from evoagent.tools.executor import ToolExecutor
from evoagent.tools.registry import ToolRegistry


class AgentLoop:
    """重复调用模型和工具，直到得到答案或达到运行限制。"""

    def __init__(
        self,
        provider: ModelProvider,
        registry: ToolRegistry,
        executor: ToolExecutor,
        event_sink: RuntimeEventSink,
        *,
        model: str,
        max_iterations: int,
        max_total_tokens: int,
        max_repeated_tool_calls: int = 3,
        temperature: float | None = None,
        max_output_tokens: int | None = None,
    ) -> None:
        normalized_model = model.strip()
        if not normalized_model:
            raise ValueError("model cannot be blank")
        if max_iterations < 1:
            raise ValueError("max_iterations must be positive")
        if max_total_tokens < 1:
            raise ValueError("max_total_tokens must be positive")
        if max_repeated_tool_calls < 1:
            raise ValueError("max_repeated_tool_calls must be positive")

        self._provider = provider
        self._registry = registry
        self._executor = executor
        self._event_sink = event_sink
        self._model = normalized_model
        self._max_iterations = max_iterations
        self._max_total_tokens = max_total_tokens
        self._max_repeated_tool_calls = max_repeated_tool_calls
        self._temperature = temperature
        self._max_output_tokens = max_output_tokens

    async def run(self, initial_messages: tuple[Message, ...]) -> AgentLoopResult:
        """执行一次模型—工具循环；任务级生命周期由 AgentRunner 管理。"""

        if not initial_messages:
            raise ValueError("initial_messages cannot be empty")

        messages = list(initial_messages)
        known_usage = TokenUsage(input_tokens=0, output_tokens=0, total_tokens=0)
        usage_is_complete = True
        previous_tool_fingerprint: str | None = None
        repeated_tool_calls = 0

        for iteration in range(1, self._max_iterations + 1):
            request = ModelRequest(
                messages=tuple(messages),
                tool_definitions=self._registry.definitions(),
                model=self._model,
                temperature=self._temperature,
                max_output_tokens=self._max_output_tokens,
            )
            await self._event_sink.emit(
                EventType.MODEL_REQUESTED,
                {
                    "iteration": iteration,
                    "model": self._model,
                    "message_count": len(request.messages),
                    "tool_count": len(request.tool_definitions),
                },
            )

            try:
                response, response_usage = await self._consume_response(request, iteration)
            except asyncio.CancelledError:
                raise
            except ProviderError as error:
                await self._event_sink.emit(
                    EventType.MODEL_FAILED,
                    {
                        "iteration": iteration,
                        "error_code": error.code,
                        "error_message": str(error),
                    },
                )
                return self._failure(
                    messages,
                    iteration,
                    known_usage if usage_is_complete else None,
                    error.code,
                    str(error),
                )
            except Exception as error:
                await self._event_sink.emit(
                    EventType.MODEL_FAILED,
                    {
                        "iteration": iteration,
                        "error_code": "internal_provider_error",
                        "error_type": type(error).__name__,
                    },
                )
                raise

            if response_usage is None:
                usage_is_complete = False
            else:
                known_usage = known_usage + response_usage

            await self._event_sink.emit(
                EventType.MODEL_COMPLETED,
                {
                    "iteration": iteration,
                    "finish_reason": response.finish_reason.value,
                    "tool_call_count": len(response.message.tool_calls),
                    "usage": response_usage.model_dump(mode="json") if response_usage else None,
                },
            )
            messages.append(response.message)

            if response.message.tool_calls:
                results = await self._executor.execute_many(response.message.tool_calls)
                messages.extend(self._tool_message(result) for result in results)

                fingerprint = self._tool_fingerprint(response.message.tool_calls, results)
                if fingerprint == previous_tool_fingerprint:
                    repeated_tool_calls += 1
                else:
                    previous_tool_fingerprint = fingerprint
                    repeated_tool_calls = 1
                if repeated_tool_calls >= self._max_repeated_tool_calls:
                    return AgentLoopResult(
                        status=AgentLoopStatus.LIMIT_REACHED,
                        messages=tuple(messages),
                        iterations=iteration,
                        usage=known_usage if usage_is_complete else None,
                        error_code="repeated_tool_calls",
                        error_message=(
                            "identical tool calls produced identical results "
                            f"{repeated_tool_calls} times"
                        ),
                    )

                if usage_is_complete and known_usage.total_tokens >= self._max_total_tokens:
                    return AgentLoopResult(
                        status=AgentLoopStatus.LIMIT_REACHED,
                        messages=tuple(messages),
                        iterations=iteration,
                        usage=known_usage,
                        error_code="token_budget_reached",
                        error_message=(
                            f"token budget reached: {known_usage.total_tokens} "
                            f">= {self._max_total_tokens}"
                        ),
                    )
                continue

            if response.finish_reason is FinishReason.STOP:
                answer = response.message.content
                if answer is not None and answer.strip():
                    return AgentLoopResult(
                        status=AgentLoopStatus.COMPLETED,
                        messages=tuple(messages),
                        iterations=iteration,
                        usage=known_usage if usage_is_complete else None,
                        final_answer=answer,
                    )
                error_code = "empty_final_answer"
                error_message = "model stopped without a non-blank final answer"
            else:
                error_code = f"model_finish_{response.finish_reason.value}"
                error_message = (
                    "model returned no tool calls and did not finish normally: "
                    f"{response.finish_reason.value}"
                )
            return self._failure(
                messages,
                iteration,
                known_usage if usage_is_complete else None,
                error_code,
                error_message,
            )

        return AgentLoopResult(
            status=AgentLoopStatus.LIMIT_REACHED,
            messages=tuple(messages),
            iterations=self._max_iterations,
            usage=known_usage if usage_is_complete else None,
            error_code="max_iterations_reached",
            error_message=f"maximum iterations reached: {self._max_iterations}",
        )

    async def _consume_response(
        self, request: ModelRequest, iteration: int
    ) -> tuple[ModelResponse, TokenUsage | None]:
        completed_response: ModelResponse | None = None
        streamed_usage: TokenUsage | None = None

        async for event in self._provider.stream(request):
            if completed_response is not None:
                raise ProviderProtocolError("provider emitted an event after completed")

            if event.type is ProviderEventType.TEXT_DELTA:
                await self._event_sink.emit(
                    EventType.MODEL_DELTA,
                    {
                        "iteration": iteration,
                        "kind": event.type.value,
                        "text_delta": event.text_delta,
                    },
                )
            elif event.type is ProviderEventType.TOOL_CALL_DELTA:
                await self._event_sink.emit(
                    EventType.MODEL_DELTA,
                    {
                        "iteration": iteration,
                        "kind": event.type.value,
                        "tool_call_index": event.tool_call_index,
                        "tool_call_id": event.tool_call_id,
                        "tool_name_delta": event.tool_name_delta,
                    },
                )
            elif event.type is ProviderEventType.USAGE:
                if streamed_usage is not None:
                    raise ProviderProtocolError("provider emitted more than one usage event")
                streamed_usage = event.usage
            elif event.type is ProviderEventType.COMPLETED:
                completed_response = event.response

        if completed_response is None:
            raise ProviderProtocolError("provider stream ended without a completed event")

        response_usage = completed_response.usage
        if (
            streamed_usage is not None
            and response_usage is not None
            and streamed_usage != response_usage
        ):
            raise ProviderProtocolError("streamed usage does not match completed response usage")
        return completed_response, response_usage or streamed_usage

    @staticmethod
    def _tool_message(result: ToolResult) -> Message:
        if result.status is ToolResultStatus.SUCCESS:
            content = result.content
        else:
            content = f"工具调用失败（{result.error_code}）：{result.content}"
        return Message(
            role=MessageRole.TOOL,
            content=content,
            tool_call_id=result.tool_call_id,
        )

    @staticmethod
    def _tool_fingerprint(calls: tuple[ToolCall, ...], results: tuple[ToolResult, ...]) -> str:
        normalized = [
            {
                "name": call.name,
                "arguments": call.arguments,
                "status": result.status.value,
                "content": result.content,
                "error_code": result.error_code,
            }
            for call, result in zip(calls, results, strict=True)
        ]
        return json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    @staticmethod
    def _failure(
        messages: list[Message],
        iteration: int,
        usage: TokenUsage | None,
        error_code: str,
        error_message: str,
    ) -> AgentLoopResult:
        return AgentLoopResult(
            status=AgentLoopStatus.FAILED,
            messages=tuple(messages),
            iterations=iteration,
            usage=usage,
            error_code=error_code,
            error_message=error_message,
        )
