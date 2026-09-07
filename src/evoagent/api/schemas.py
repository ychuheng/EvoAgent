"""HTTP 层使用的请求、响应与错误契约。"""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from evoagent.db.models import ApprovalStatus
from evoagent.tasks.state_machine import PersistentRunStatus, TaskStatus


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ErrorDetail(ApiModel):
    code: str
    message: str
    details: dict[str, Any] | None = None


class ErrorResponse(ApiModel):
    error: ErrorDetail


class HealthResponse(ApiModel):
    status: str


class SessionCreateRequest(ApiModel):
    title: str = Field(min_length=1, max_length=256)

    @field_validator("title")
    @classmethod
    def normalize_title(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("session title cannot be blank")
        return normalized


class SessionResponse(ApiModel):
    id: UUID
    title: str
    created_at: datetime


class TaskCreateRequest(ApiModel):
    session_id: UUID
    goal: str = Field(min_length=1, max_length=100_000)
    provider: str | None = Field(default=None, min_length=1, max_length=64)
    model: str | None = Field(default=None, min_length=1, max_length=256)

    @field_validator("goal")
    @classmethod
    def normalize_goal(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("task goal cannot be blank")
        return normalized

    @field_validator("provider", "model")
    @classmethod
    def normalize_optional_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("provider and model cannot be blank")
        return normalized


class RunResponse(ApiModel):
    id: UUID
    status: PersistentRunStatus
    provider: str
    model: str
    created_at: datetime
    updated_at: datetime


class TaskResponse(ApiModel):
    id: UUID
    session_id: UUID
    goal: str
    status: TaskStatus
    cancel_requested: bool
    attempt_count: int
    created_at: datetime
    updated_at: datetime
    latest_run: RunResponse


class ApprovalDecisionRequest(ApiModel):
    response: str | None = Field(default=None, max_length=20_000)


class ApprovalResponse(ApiModel):
    id: UUID
    task_id: UUID
    tool_call_id: UUID
    status: ApprovalStatus
    risk: str
    reason: str
    response: str | None
    requested_at: datetime
    decided_at: datetime | None
