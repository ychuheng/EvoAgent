import asyncio

import pytest
from pydantic import ValidationError

from evoagent.config import Settings
from evoagent.core.context import ContextBuilder
from evoagent.core.context_budget import ConservativeTokenCounter, ContextBudget, MockCounter
from evoagent.core.context_policy import (
    BoundedContextPolicy,
    ContextPolicyError,
    MessageGroupBuilder,
)
from evoagent.core.models import (
    FinishReason,
    Message,
    ModelRequest,
    ModelResponse,
    ToolCall,
    ToolDefinition,
)
from evoagent.core.runner import AgentRunner
from evoagent.providers.mock import MockProvider
from evoagent.tools.builtin.calculator import CalculatorTool
from evoagent.tools.registry import ToolRegistry


def request(messages=None, tools=()):
    return ModelRequest(
        model="mock-model",
        messages=messages or ContextBuilder().build("保留目标"),
        tool_definitions=tools,
    )


def policy(window=2000, counter=None, strict=False):
    return BoundedContextPolicy(
        ContextBudget(context_window=window, output_tokens=100, safety_margin=100),
        counter or MockCounter(),
        strict=strict,
    )


@pytest.mark.parametrize(
    "text", ["资料" * 3000, "external context " * 3000], ids=["cjk", "english"]
)
def test_trim_optional_only_and_preserve_original_constraints(text):
    initial = ContextBuilder().build(
        "必须保留原始目标，禁止改写", external_context=[text, "短资料"]
    )
    result = policy().prepare(request(initial))
    assert result.dropped_indices == (1,)
    assert result.request.messages == (initial[0], initial[2], initial[3])
    assert result.request.max_output_tokens == 100
    assert result.estimate.count <= result.input_limit
    assert policy().prepare(request(initial)) == result


def test_tools_count_and_cannot_be_trimmed():
    tools = tuple(
        ToolDefinition(name=f"tool{i}", description="D" * 1000, parameters={"type": "object"})
        for i in range(20)
    )
    assert MockCounter().count_request(request(tools=tools)).count > 20000
    with pytest.raises(ContextPolicyError, match="protected"):
        policy().prepare(request(tools=tools))


def test_user_cannot_spoof_optional_block_with_text_prefix():
    with pytest.raises(ContextPolicyError):
        policy().prepare(request(ContextBuilder().build("外部上下文 1：" + "x" * 5000)))
    with pytest.raises(ValidationError):
        Message(role="system", content="rules", context_priority=0)


def test_complete_tool_groups_are_retained_or_rejected_whole():
    base = ContextBuilder().build("目标")
    calls = (
        ToolCall(call_id="a", name="calculator", arguments={}),
        ToolCall(call_id="b", name="calculator", arguments={}),
    )
    group = (
        Message(role="assistant", tool_calls=calls),
        Message(role="tool", tool_call_id="b", content="ok"),
        Message(role="tool", tool_call_id="a", content="ok"),
    )
    assert MessageGroupBuilder.build(base + group)[-1] == (2, 3, 4)
    assert policy().prepare(request(base + group)).request.messages == base + group
    for invalid in (base + group[:-1], base + group[1:], base + group + group):
        with pytest.raises(ContextPolicyError):
            policy().prepare(request(invalid))


def test_unknown_tokenizer_is_estimated_and_strict_mode_rejects():
    assert (
        policy(counter=ConservativeTokenCounter()).prepare(request()).estimate.confidence
        == "estimated"
    )
    with pytest.raises(ContextPolicyError, match="verified"):
        policy(counter=ConservativeTokenCounter(), strict=True).prepare(request())
    with pytest.raises(ValidationError):
        ContextBudget(context_window=100, output_tokens=100)


async def test_each_loop_request_is_checked_including_after_large_tool_result(tmp_path):
    provider = MockProvider(
        [
            ModelResponse(
                message=Message(
                    role="assistant",
                    tool_calls=(
                        ToolCall(call_id="a", name="calculator", arguments={"expression": "1+1"}),
                    ),
                ),
                finish_reason=FinishReason.TOOL_CALLS,
            ),
            ModelResponse(
                message=Message(role="assistant", content="2"), finish_reason=FinishReason.STOP
            ),
        ]
    )
    settings = Settings(_env_file=None, workspace=tmp_path)
    result = await AgentRunner(
        settings, ContextBuilder(), provider, ToolRegistry([CalculatorTool()])
    ).run("calculate")
    checked = [e for e in result.events if e.type.value.startswith("context.")]
    requested = [e for e in result.events if e.type.value == "model.requested"]
    assert len(checked) == len(requested) == 2
    assert all(r.max_output_tokens == settings.max_output_tokens for r in provider.requests)


async def test_rejected_request_never_reaches_provider_and_legacy_can_run(tmp_path):
    response = ModelResponse(
        message=Message(role="assistant", content="done"), finish_reason="stop"
    )
    for mode, expected in (("bounded", 0), ("legacy", 1)):
        provider = MockProvider([response])
        result = await AgentRunner(
            Settings(_env_file=None, workspace=tmp_path, context_policy=mode),
            ContextBuilder(),
            provider,
            ToolRegistry(),
        ).run("x" * 40000)
        assert len(provider.requests) == expected
        assert result.error_code == ("context_budget_exceeded" if mode == "bounded" else None)


async def test_bounded_run_cancellation_still_cleans_up(tmp_path):
    entered = asyncio.Event()
    closed = asyncio.Event()

    class BlockingProvider:
        async def stream(self, request):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                closed.set()
            if False:
                yield

    runner = AgentRunner(
        Settings(_env_file=None, workspace=tmp_path),
        ContextBuilder(),
        BlockingProvider(),
        ToolRegistry(),
    )
    task = asyncio.create_task(runner.run("test"))
    await asyncio.wait_for(entered.wait(), 2)
    task.cancel()
    assert (await task).status.value == "cancelled"
    assert closed.is_set()


async def test_second_request_is_blocked_when_protected_tool_result_exceeds_window(tmp_path):
    class LargeCalculator(CalculatorTool):
        async def invoke(self, arguments):
            return "计算结果 " * 2000

    provider = MockProvider(
        [
            ModelResponse(
                message=Message(
                    role="assistant",
                    tool_calls=(
                        ToolCall(
                            call_id="large", name="calculator", arguments={"expression": "1+1"}
                        ),
                    ),
                ),
                finish_reason="tool_calls",
            )
        ]
    )
    result = await AgentRunner(
        Settings(
            _env_file=None,
            workspace=tmp_path,
            context_window_tokens=4000,
            max_output_tokens=100,
            context_safety_margin=100,
        ),
        ContextBuilder(),
        provider,
        ToolRegistry([LargeCalculator()]),
    ).run("计算")
    assert result.error_code == "context_budget_exceeded"
    assert len(provider.requests) == 1


def test_lower_priority_optional_partition_is_removed_first():
    messages = (
        Message(role="system", content="rules"),
        Message(role="user", content="higher" * 50, context_priority=10),
        Message(role="user", content="lower" * 1000, context_priority=0),
        Message(role="user", content="must preserve task"),
    )
    decision = policy().prepare(request(messages))
    assert decision.dropped_indices == (2,)
    assert decision.request.messages == (messages[0], messages[1], messages[3])
