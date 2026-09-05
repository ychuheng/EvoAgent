import asyncio
from collections.abc import AsyncIterator

import pytest

from evoagent.config import Settings
from evoagent.core.context import ContextBuilder
from evoagent.core.models import (
    EventType,
    FinishReason,
    Message,
    MessageRole,
    ModelRequest,
    ModelResponse,
    ProviderEvent,
    RunStatus,
    ToolCall,
)
from evoagent.core.runner import AgentRunner
from evoagent.providers.mock import MockProvider
from evoagent.tools.builtin.calculator import CalculatorTool
from evoagent.tools.registry import ToolRegistry


class BlockingProvider:
    async def stream(self, request: ModelRequest) -> AsyncIterator[ProviderEvent]:
        await asyncio.sleep(60)
        if False:
            yield ProviderEvent.model_construct()


def text_response(content: str) -> ModelResponse:
    return ModelResponse(
        message=Message(role=MessageRole.ASSISTANT, content=content),
        finish_reason=FinishReason.STOP,
    )


def tool_response(call_id: str) -> ModelResponse:
    return ModelResponse(
        message=Message(
            role=MessageRole.ASSISTANT,
            tool_calls=(
                ToolCall(
                    call_id=call_id,
                    name="calculator",
                    arguments={"expression": "1 + 1"},
                ),
            ),
        ),
        finish_reason=FinishReason.TOOL_CALLS,
    )


def make_runner(provider, **settings_overrides: object) -> AgentRunner:
    settings = Settings(_env_file=None, **settings_overrides)
    return AgentRunner(
        settings,
        ContextBuilder(),
        provider,
        ToolRegistry([CalculatorTool()]),
    )


@pytest.mark.asyncio
async def test_runner_returns_completed_result_with_one_terminal_event() -> None:
    runner = make_runner(MockProvider([text_response("done")]))

    result = await runner.run("task")

    assert result.status is RunStatus.COMPLETED
    assert result.final_answer == "done"
    assert result.events[0].type is EventType.RUN_STARTED
    assert result.events[-1].type is EventType.RUN_COMPLETED
    assert sum(event.type.value.startswith("run.") for event in result.events[1:]) == 1


@pytest.mark.asyncio
async def test_runner_maps_loop_failure_to_failed_result() -> None:
    partial = ModelResponse(
        message=Message(role=MessageRole.ASSISTANT, content="partial"),
        finish_reason=FinishReason.LENGTH,
    )
    runner = make_runner(MockProvider([partial]))

    result = await runner.run("task")

    assert result.status is RunStatus.FAILED
    assert result.error_code == "model_finish_length"
    assert result.events[-1].type is EventType.RUN_FAILED


@pytest.mark.asyncio
async def test_runner_maps_loop_limit_to_limit_result() -> None:
    runner = make_runner(MockProvider([tool_response("call-1")]), max_iterations=1)

    result = await runner.run("task")

    assert result.status is RunStatus.LIMIT_REACHED
    assert result.error_code == "max_iterations_reached"
    assert result.events[-1].type is EventType.RUN_LIMIT_REACHED


@pytest.mark.asyncio
async def test_runner_enforces_task_timeout() -> None:
    runner = make_runner(BlockingProvider(), task_timeout_seconds=0.01)

    result = await runner.run("task")

    assert result.status is RunStatus.TIMEOUT
    assert result.error_code == "run_timeout"
    assert result.events[-1].type is EventType.RUN_TIMEOUT


@pytest.mark.asyncio
async def test_runner_converts_cancellation_to_cancelled_result() -> None:
    runner = make_runner(BlockingProvider(), task_timeout_seconds=10)
    task = asyncio.create_task(runner.run("task"))
    await asyncio.sleep(0)

    task.cancel()
    result = await task

    assert result.status is RunStatus.CANCELLED
    assert result.error_code == "run_cancelled"
    assert result.events[-1].type is EventType.RUN_CANCELLED


@pytest.mark.asyncio
async def test_runner_rejects_blank_input_as_run_failure() -> None:
    runner = make_runner(MockProvider([]))

    result = await runner.run("  ")

    assert result.status is RunStatus.FAILED
    assert result.error_code == "invalid_input"
    assert result.events[-1].type is EventType.RUN_FAILED
