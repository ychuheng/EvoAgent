"""请求输入预算；本地估算与服务端 usage 明确分开。"""

import json
from dataclasses import dataclass
from typing import Protocol

from pydantic import Field, model_validator

from evoagent.core.models import ContractModel, ModelRequest


class ContextBudget(ContractModel):
    context_window: int = Field(default=32768, gt=0)
    output_tokens: int = Field(default=4096, gt=0)
    safety_margin: int = Field(default=1024, ge=0)

    @model_validator(mode="after")
    def validate_capacity(self):
        if self.input_limit <= 0:
            raise ValueError("context_window must exceed output_tokens + safety_margin")
        return self

    @property
    def input_limit(self) -> int:
        return self.context_window - self.output_tokens - self.safety_margin


@dataclass(frozen=True)
class TokenEstimate:
    count: int
    method: str
    tokenizer_version: str
    confidence: str


class TokenCounter(Protocol):
    identity: str

    def count_request(self, request: ModelRequest) -> TokenEstimate: ...


def request_bytes(request: ModelRequest) -> int:
    # 使用完整工具 Schema、参数字符串及 function 包装；不计算内部裁剪元数据。
    messages = []
    for message in request.messages:
        item = {"role": message.role.value, "content": message.content}
        if message.tool_call_id is not None:
            item["tool_call_id"] = message.tool_call_id
        if message.tool_calls:
            item["tool_calls"] = [
                {
                    "id": call.call_id,
                    "type": "function",
                    "function": {
                        "name": call.name,
                        "arguments": json.dumps(call.arguments, ensure_ascii=False),
                    },
                }
                for call in message.tool_calls
            ]
        messages.append(item)
    payload = {
        "model": request.model,
        "messages": messages,
        "tools": [
            {"type": "function", "function": tool.model_dump(mode="json")}
            for tool in request.tool_definitions
        ],
    }
    return len(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8"))


class ConservativeTokenCounter:
    """UTF-8 字节估算 + 封装余量；不宣称未知服务的 tokenizer 上界。"""

    identity = "utf8-envelope-v1"

    def count_request(self, request: ModelRequest) -> TokenEstimate:
        count = request_bytes(request) + 32 * (
            len(request.messages) + len(request.tool_definitions) + 1
        )
        return TokenEstimate(count, self.identity, "1", "estimated")


class MockCounter(ConservativeTokenCounter):
    """离线 fixture 的定义计数规则，仅对 Mock 有精确含义。"""

    identity = "mock-utf8-envelope-v1"

    def count_request(self, request: ModelRequest) -> TokenEstimate:
        estimate = super().count_request(request)
        return TokenEstimate(estimate.count, self.identity, "1", "exact_mock")
