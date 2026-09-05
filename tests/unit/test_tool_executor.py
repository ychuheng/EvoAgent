import asyncio
from uuid import uuid4

import pytest

from evoagent.core.events import InMemoryEventSink
from evoagent.core.models import (
    ContractModel,
    EventType,
    ToolCall,
    ToolResultStatus,
    ToolRisk,
)
from evoagent.tools.base import BaseTool
from evoagent.tools.builtin.calculator import CalculatorTool
from evoagent.tools.executor import ToolExecutor
from evoagent.tools.registry import ToolRegistry


class EmptyArguments(ContractModel):
    pass


class SlowTool(BaseTool[EmptyArguments]):
    name = "slow"
    description = "Wait until the configured timeout expires."
    arguments_model = EmptyArguments
    risk = ToolRisk.R0
    has_side_effects = False
    parallel_safe = True

    async def invoke(self, arguments: EmptyArguments) -> str:
        await asyncio.sleep(60)
        return "unreachable"


class LongResultTool(BaseTool[EmptyArguments]):
    name = "long_result"
    description = "Return a deliberately long string."
    arguments_model = EmptyArguments
    risk = ToolRisk.R0
    has_side_effects = False
    parallel_safe = True

    async def invoke(self, arguments: EmptyArguments) -> str:
        return "abcdefghijk"


class BrokenTool(BaseTool[EmptyArguments]):
    name = "broken"
    description = "Raise an unexpected implementation error."
    arguments_model = EmptyArguments
    risk = ToolRisk.R0
    has_side_effects = False
    parallel_safe = True

    async def invoke(self, arguments: EmptyArguments) -> str:
        raise RuntimeError("implementation bug")


def make_executor(
    *tools: BaseTool, timeout_seconds: float = 1, max_result_chars: int = 1_000
) -> tuple[ToolExecutor, InMemoryEventSink]:
    sink = InMemoryEventSink(uuid4())
    executor = ToolExecutor(
        ToolRegistry(tools),
        sink,
        timeout_seconds=timeout_seconds,
        max_result_chars=max_result_chars,
    )
    return executor, sink


@pytest.mark.asyncio
async def test_executor_validates_invokes_and_emits_success_events() -> None:
    executor, sink = make_executor(CalculatorTool())
    call = ToolCall(call_id="call-1", name="calculator", arguments={"expression": "2+3"})

    result = await executor.execute(call)

    assert result.status is ToolResultStatus.SUCCESS
    assert result.content == "5"
    assert result.tool_call_id == call.call_id
    assert [event.type for event in sink.events] == [
        EventType.TOOL_STARTED,
        EventType.TOOL_COMPLETED,
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("call", "error_code"),
    [
        (ToolCall(call_id="missing", name="unknown"), "tool_not_found"),
        (
            ToolCall(call_id="invalid", name="calculator", arguments={}),
            "invalid_arguments",
        ),
        (
            ToolCall(
                call_id="failure",
                name="calculator",
                arguments={"expression": "1 / 0"},
            ),
            "tool_execution_error",
        ),
    ],
)
async def test_executor_converts_recoverable_failures_to_results(
    call: ToolCall, error_code: str
) -> None:
    executor, sink = make_executor(CalculatorTool())

    result = await executor.execute(call)

    assert result.status is ToolResultStatus.ERROR
    assert result.error_code == error_code
    assert result.tool_call_id == call.call_id
    assert sink.events[-1].type is EventType.TOOL_FAILED


@pytest.mark.asyncio
async def test_executor_converts_timeout_to_result() -> None:
    executor, _ = make_executor(SlowTool(), timeout_seconds=0.01)

    result = await executor.execute(ToolCall(call_id="slow-1", name="slow"))

    assert result.status is ToolResultStatus.ERROR
    assert result.error_code == "tool_timeout"


@pytest.mark.asyncio
async def test_executor_limits_result_length_and_marks_event() -> None:
    executor, sink = make_executor(LongResultTool(), max_result_chars=10)

    result = await executor.execute(ToolCall(call_id="long-1", name="long_result"))

    assert len(result.content) == 10
    assert sink.events[-1].payload["truncated"] is True


@pytest.mark.asyncio
async def test_execute_many_preserves_original_order() -> None:
    executor, _ = make_executor(CalculatorTool())
    calls = (
        ToolCall(call_id="call-2", name="calculator", arguments={"expression": "2+2"}),
        ToolCall(call_id="call-1", name="calculator", arguments={"expression": "1+1"}),
    )

    results = await executor.execute_many(calls)

    assert [result.tool_call_id for result in results] == ["call-2", "call-1"]
    assert [result.content for result in results] == ["4", "2"]


@pytest.mark.asyncio
async def test_executor_propagates_unexpected_tool_errors() -> None:
    executor, sink = make_executor(BrokenTool())

    with pytest.raises(RuntimeError, match="implementation bug"):
        await executor.execute(ToolCall(call_id="broken-1", name="broken"))

    assert sink.events[-1].payload["error_code"] == "internal_tool_error"


@pytest.mark.asyncio
async def test_executor_propagates_cancellation() -> None:
    executor, _ = make_executor(SlowTool(), timeout_seconds=10)
    task = asyncio.create_task(executor.execute(ToolCall(call_id="slow-1", name="slow")))
    await asyncio.sleep(0)

    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task


def test_executor_rejects_invalid_limits() -> None:
    sink = InMemoryEventSink(uuid4())
    registry = ToolRegistry()

    with pytest.raises(ValueError, match="timeout_seconds"):
        ToolExecutor(registry, sink, timeout_seconds=0, max_result_chars=10)
    with pytest.raises(ValueError, match="max_result_chars"):
        ToolExecutor(registry, sink, timeout_seconds=1, max_result_chars=0)
