"""运行时各组件共享的、经过校验的数据契约。"""

from datetime import datetime
from enum import StrEnum
from typing import Any, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

TOOL_NAME_PATTERN = r"^[A-Za-z][A-Za-z0-9_-]{0,63}$"


class ContractModel(BaseModel):
    """不允许额外字段且顶层字段不可重新赋值的严格基类。"""

    model_config = ConfigDict(extra="forbid", frozen=True)


class MessageRole(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class FinishReason(StrEnum):
    STOP = "stop"
    TOOL_CALLS = "tool_calls"
    LENGTH = "length"
    CONTENT_FILTER = "content_filter"
    ERROR = "error"


class ToolRisk(StrEnum):
    R0 = "R0"
    R1 = "R1"
    R2 = "R2"
    R3 = "R3"


class ToolResultStatus(StrEnum):
    SUCCESS = "success"
    ERROR = "error"
    PERMISSION_DENIED = "permission_denied"


class ProviderEventType(StrEnum):
    TEXT_DELTA = "text_delta"
    TOOL_CALL_DELTA = "tool_call_delta"
    USAGE = "usage"
    COMPLETED = "completed"


class RunStatus(StrEnum):
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"
    LIMIT_REACHED = "limit_reached"


class AgentLoopStatus(StrEnum):
    COMPLETED = "completed"
    FAILED = "failed"
    LIMIT_REACHED = "limit_reached"


class EventType(StrEnum):
    RUN_STARTED = "run.started"
    MODEL_REQUESTED = "model.requested"
    MODEL_DELTA = "model.delta"
    MODEL_COMPLETED = "model.completed"
    MODEL_FAILED = "model.failed"
    TOOL_STARTED = "tool.started"
    TOOL_COMPLETED = "tool.completed"
    TOOL_FAILED = "tool.failed"
    RUN_COMPLETED = "run.completed"
    RUN_FAILED = "run.failed"
    RUN_CANCELLED = "run.cancelled"
    RUN_TIMEOUT = "run.timeout"
    RUN_LIMIT_REACHED = "run.limit_reached"
    RECOVERY_COMPLETED = "recovery.completed"


class ToolDefinition(ContractModel):
    """提供给模型的工具元数据和 JSON Schema。"""

    name: str = Field(pattern=TOOL_NAME_PATTERN)
    description: str = Field(min_length=1, max_length=1_024)
    parameters: dict[str, Any]


class ToolCall(ContractModel):
    """由模型生成并已完成组装的工具调用请求。"""

    call_id: str = Field(min_length=1, max_length=256)
    name: str = Field(pattern=TOOL_NAME_PATTERN)
    arguments: dict[str, JsonValue] = Field(default_factory=dict)


class ToolResult(ContractModel):
    """与某一次 ToolCall 一一对应的工具结果。"""

    tool_call_id: str = Field(min_length=1, max_length=256)
    name: str = Field(pattern=TOOL_NAME_PATTERN)
    status: ToolResultStatus
    content: str
    error_code: str | None = None

    @model_validator(mode="after")
    def validate_error_code(self) -> Self:
        if self.status is ToolResultStatus.SUCCESS and self.error_code is not None:
            raise ValueError("a successful ToolResult cannot contain error_code")
        if self.status is not ToolResultStatus.SUCCESS and not self.error_code:
            raise ValueError("a failed ToolResult must contain error_code")
        return self


class Message(ContractModel):
    """与具体模型服务无关的聊天消息。"""

    role: MessageRole
    content: str | None = None
    tool_calls: tuple[ToolCall, ...] = ()
    tool_call_id: str | None = None

    @model_validator(mode="after")
    def validate_role_shape(self) -> Self:
        if self.role is MessageRole.TOOL:
            if self.content is None or not self.tool_call_id:
                raise ValueError("tool messages require content and tool_call_id")
            if self.tool_calls:
                raise ValueError("tool messages cannot contain tool_calls")
            return self

        if self.tool_call_id is not None:
            raise ValueError("only tool messages may contain tool_call_id")

        if self.role is MessageRole.ASSISTANT:
            if self.content is None and not self.tool_calls:
                raise ValueError("assistant messages require content or tool_calls")
            return self

        if self.tool_calls:
            raise ValueError("only assistant messages may contain tool_calls")
        if self.content is None or not self.content.strip():
            raise ValueError("system and user messages require non-blank content")
        return self


class TokenUsage(ContractModel):
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_total(self) -> Self:
        if self.total_tokens != self.input_tokens + self.output_tokens:
            raise ValueError("total_tokens must equal input_tokens + output_tokens")
        return self

    def __add__(self, other: "TokenUsage") -> "TokenUsage":
        return TokenUsage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            total_tokens=self.total_tokens + other.total_tokens,
        )


class ModelRequest(ContractModel):
    messages: tuple[Message, ...] = Field(min_length=1)
    tool_definitions: tuple[ToolDefinition, ...] = ()
    model: str = Field(min_length=1)
    temperature: float | None = Field(default=None, ge=0, le=2)
    max_output_tokens: int | None = Field(default=None, ge=1)


class ModelResponse(ContractModel):
    message: Message
    finish_reason: FinishReason
    usage: TokenUsage | None = None

    @model_validator(mode="after")
    def validate_assistant_message(self) -> Self:
        if self.message.role is not MessageRole.ASSISTANT:
            raise ValueError("ModelResponse.message must have the assistant role")
        if self.message.tool_calls and self.finish_reason is not FinishReason.TOOL_CALLS:
            raise ValueError("responses with tool calls require finish_reason=tool_calls")
        if self.finish_reason is FinishReason.TOOL_CALLS and not self.message.tool_calls:
            raise ValueError("finish_reason=tool_calls requires at least one ToolCall")
        return self


class ProviderEvent(ContractModel):
    """模型服务异步事件流中经过标准化的一项事件。"""

    type: ProviderEventType
    text_delta: str | None = None
    tool_call_index: int | None = Field(default=None, ge=0)
    tool_call_id: str | None = None
    tool_name_delta: str | None = None
    arguments_delta: str | None = None
    usage: TokenUsage | None = None
    response: ModelResponse | None = None

    @model_validator(mode="after")
    def validate_event_shape(self) -> Self:
        if self.type is ProviderEventType.TEXT_DELTA:
            if self.text_delta is None:
                raise ValueError("text_delta events require text_delta")
            if any(
                value is not None
                for value in (
                    self.tool_call_index,
                    self.tool_call_id,
                    self.tool_name_delta,
                    self.arguments_delta,
                    self.usage,
                    self.response,
                )
            ):
                raise ValueError("text_delta events contain fields for another event type")
        elif self.type is ProviderEventType.TOOL_CALL_DELTA:
            fragments = (self.tool_call_id, self.tool_name_delta, self.arguments_delta)
            if self.tool_call_index is None or all(fragment is None for fragment in fragments):
                raise ValueError(
                    "tool_call_delta events require an index and at least one fragment"
                )
            if self.text_delta is not None or self.usage is not None or self.response is not None:
                raise ValueError("tool_call_delta events contain fields for another event type")
        elif self.type is ProviderEventType.USAGE:
            if self.usage is None:
                raise ValueError("usage events require usage")
            if any(
                value is not None
                for value in (
                    self.text_delta,
                    self.tool_call_index,
                    self.tool_call_id,
                    self.tool_name_delta,
                    self.arguments_delta,
                    self.response,
                )
            ):
                raise ValueError("usage events contain fields for another event type")
        elif self.type is ProviderEventType.COMPLETED:
            if self.response is None:
                raise ValueError("completed events require response")
            if any(
                value is not None
                for value in (
                    self.text_delta,
                    self.tool_call_index,
                    self.tool_call_id,
                    self.tool_name_delta,
                    self.arguments_delta,
                    self.usage,
                )
            ):
                raise ValueError("completed events contain fields for another event type")
        return self


class RuntimeEvent(ContractModel):
    """一次 Run 执行过程中发出的可观测事件。"""

    run_id: UUID
    sequence: int = Field(ge=1)
    type: EventType
    timestamp: datetime
    payload: dict[str, JsonValue] = Field(default_factory=dict)
    schema_version: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def validate_timezone(self) -> Self:
        if self.timestamp.tzinfo is None or self.timestamp.utcoffset() is None:
            raise ValueError("RuntimeEvent.timestamp must be timezone-aware")
        return self


class AgentLoopResult(ContractModel):
    """AgentLoop 返回给未来 AgentRunner 的循环结果。"""

    status: AgentLoopStatus
    messages: tuple[Message, ...] = Field(min_length=1)
    iterations: int = Field(ge=1)
    usage: TokenUsage | None = None
    final_answer: str | None = None
    error_code: str | None = None
    error_message: str | None = None

    @model_validator(mode="after")
    def validate_outcome(self) -> Self:
        if self.status is AgentLoopStatus.COMPLETED:
            if self.final_answer is None or not self.final_answer.strip():
                raise ValueError("completed loops require a non-blank final_answer")
            if self.error_code is not None or self.error_message is not None:
                raise ValueError("completed loops cannot contain an error")
        else:
            if not self.error_code:
                raise ValueError("non-completed loops require error_code")
            if self.final_answer is not None:
                raise ValueError("non-completed loops cannot contain final_answer")
        return self


class LoopState(ContractModel):
    """只能在完整模型—工具边界保存的可恢复循环状态。"""

    messages: tuple[Message, ...] = Field(min_length=1)
    completed_iterations: int = Field(ge=0)
    usage: TokenUsage | None = None
    usage_is_complete: bool = True
    previous_tool_fingerprint: str | None = None
    repeated_tool_calls: int = Field(default=0, ge=0)
    config_hash: str = Field(min_length=1, max_length=128)

    @model_validator(mode="after")
    def validate_usage(self) -> Self:
        if self.usage_is_complete and self.usage is None:
            raise ValueError("complete usage state requires usage")
        if self.repeated_tool_calls > 0 and self.previous_tool_fingerprint is None:
            raise ValueError("repeated tool calls require a fingerprint")
        return self


class RunResult(ContractModel):
    """AgentRunner 返回的最终运行摘要。"""

    run_id: UUID
    status: RunStatus
    final_answer: str | None = None
    usage: TokenUsage | None
    events: tuple[RuntimeEvent, ...] = Field(min_length=1)
    error_code: str | None = None
    error_message: str | None = None

    @model_validator(mode="after")
    def validate_result(self) -> Self:
        if self.status is RunStatus.COMPLETED:
            if self.final_answer is None or not self.final_answer.strip():
                raise ValueError("completed runs require a non-blank final_answer")
            if self.error_code is not None or self.error_message is not None:
                raise ValueError("completed runs cannot contain an error")
        elif not self.error_code:
            raise ValueError("non-completed runs require error_code")

        if any(event.run_id != self.run_id for event in self.events):
            raise ValueError("all RuntimeEvents must belong to this run_id")
        sequences = [event.sequence for event in self.events]
        if sequences != list(range(1, len(sequences) + 1)):
            raise ValueError("RuntimeEvent sequences must be contiguous and ordered from 1")
        return self
