"""纯内存、无模型调用的请求前检查与可选资料裁剪。"""

from dataclasses import asdict, dataclass
from typing import Protocol

from evoagent.core.context_budget import (
    ConservativeTokenCounter,
    ContextBudget,
    MockCounter,
    TokenCounter,
    TokenEstimate,
)
from evoagent.core.models import Message, MessageRole, ModelRequest


class ContextPolicyError(ValueError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


class MessageGroupBuilder:
    """拒绝孤立结果、缺失结果、重复 call id 和被打断的工具组。"""

    @staticmethod
    def build(messages: tuple[Message, ...]) -> tuple[tuple[int, ...], ...]:
        groups = []
        index = 0
        seen = set()
        while index < len(messages):
            message = messages[index]
            if message.role is MessageRole.TOOL:
                raise ContextPolicyError("invalid_context_group", "orphan tool result")
            group = [index]
            if message.tool_calls:
                pending = {call.call_id for call in message.tool_calls}
                if len(pending) != len(message.tool_calls) or seen & pending:
                    raise ContextPolicyError("invalid_context_group", "duplicate tool call id")
                seen.update(pending)
                while pending:
                    index += 1
                    if index >= len(messages):
                        raise ContextPolicyError("invalid_context_group", "missing tool result")
                    result = messages[index]
                    if result.role is not MessageRole.TOOL or result.tool_call_id not in pending:
                        raise ContextPolicyError(
                            "invalid_context_group", "broken tool message group"
                        )
                    pending.remove(result.tool_call_id)
                    group.append(index)
            groups.append(tuple(group))
            index += 1
        return tuple(groups)


@dataclass(frozen=True)
class ContextDecision:
    request: ModelRequest
    estimate: TokenEstimate | None
    dropped_indices: tuple[int, ...] = ()
    input_limit: int | None = None

    def audit_payload(self) -> dict:
        return {
            "estimate": asdict(self.estimate) if self.estimate else None,
            "dropped_indices": self.dropped_indices,
            "input_limit": self.input_limit,
            "max_output_tokens": self.request.max_output_tokens,
        }


class ContextPolicy(Protocol):
    def prepare(self, request: ModelRequest) -> ContextDecision: ...

    def manifest(self) -> dict: ...


class LegacyContextPolicy:
    def prepare(self, request: ModelRequest) -> ContextDecision:
        return ContextDecision(request, None)

    def manifest(self) -> dict:
        return {"mode": "legacy", "version": 1}


class BoundedContextPolicy:
    def __init__(self, budget: ContextBudget, counter: TokenCounter, *, strict: bool = False):
        self.budget = budget
        self.counter = counter
        self.strict = strict

    def manifest(self) -> dict:
        return {
            "mode": "bounded",
            "version": 1,
            "budget": self.budget.model_dump(),
            "counter": self.counter.identity,
            "strict": self.strict,
        }

    def prepare(self, request: ModelRequest) -> ContextDecision:
        MessageGroupBuilder.build(request.messages)
        candidate = request.model_copy(update={"max_output_tokens": self.budget.output_tokens})
        estimate = self.counter.count_request(candidate)
        if self.strict and estimate.confidence not in (
            "exact",
            "exact_mock",
            "verified_upper_bound",
        ):
            raise ContextPolicyError(
                "context_count_unverified", "strict mode requires verified counting"
            )
        # 元数据由 ContextBuilder 标注，绝不根据用户文本前缀判断可删除性。
        removable = sorted(
            (i for i, m in enumerate(request.messages) if m.context_priority is not None),
            key=lambda i: (request.messages[i].context_priority, i),
        )
        dropped = []
        for index in removable:
            if estimate.count <= self.budget.input_limit:
                break
            dropped.append(index)
            candidate = candidate.model_copy(
                update={
                    "messages": tuple(m for i, m in enumerate(request.messages) if i not in dropped)
                }
            )
            estimate = self.counter.count_request(candidate)
        if estimate.count > self.budget.input_limit:
            raise ContextPolicyError(
                "context_budget_exceeded", "protected context exceeds input budget"
            )
        return ContextDecision(candidate, estimate, tuple(dropped), self.budget.input_limit)


def policy_from_settings(settings) -> ContextPolicy:
    if settings.context_policy == "legacy":
        return LegacyContextPolicy()
    return BoundedContextPolicy(
        ContextBudget(
            context_window=settings.context_window_tokens,
            output_tokens=settings.max_output_tokens,
            safety_margin=settings.context_safety_margin,
        ),
        MockCounter() if settings.provider.value == "mock" else ConservativeTokenCounter(),
        strict=settings.context_strict,
    )
