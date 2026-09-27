"""HTTP 层使用的请求、响应与错误契约。"""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from evoagent.db.models import ApprovalStatus
from evoagent.projects.schema import ProjectAuthorization, ProjectStatus
from evoagent.runtime.run_config import RunMode
from evoagent.tasks.acceptance import AcceptanceSpec
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


class RuntimeInfoResponse(ApiModel):
    provider_mode: str
    provider: str
    model: str
    search_mode: str
    memory_enabled: bool
    code_version: str
    remote_model_checked: bool = False
    worker_status: Literal["ready", "missing", "unknown"] = "unknown"


class SessionCreateRequest(ApiModel):
    title: str = Field(min_length=1, max_length=256)
    workspace_id: UUID | None = None
    project_id: UUID | None = None

    @field_validator("title")
    @classmethod
    def normalize_title(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("session title cannot be blank")
        return normalized


class SessionUpdateRequest(ApiModel):
    """只允许切换会话选中的项目；切换不影响在跑的 Task。"""

    project_id: UUID | None = None


class SessionResponse(ApiModel):
    id: UUID
    title: str
    workspace_id: UUID
    project_id: UUID | None = None
    created_at: datetime


class ProjectRegisterRequest(ApiModel):
    path: str = Field(min_length=1, max_length=2_048)
    name: str | None = Field(default=None, max_length=256)
    authorization: ProjectAuthorization = ProjectAuthorization.READ

    @field_validator("path")
    @classmethod
    def normalize_path(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("项目根路径不能为空")
        return normalized


class ProjectAuthorizationRequest(ApiModel):
    authorization: ProjectAuthorization


class ProjectRevokeRequest(ApiModel):
    reason: str = Field(default="", max_length=512)


class ProjectResponse(ApiModel):
    id: UUID
    name: str
    root: str
    authorization: ProjectAuthorization
    status: ProjectStatus
    authorization_version: int
    created_at: datetime
    # 实测的根可用性，与库里的 status 分开返回，便于页面提示"挂载缺失/权限不足"。
    root_available: bool | None = None


class WorkspaceCreateRequest(ApiModel):
    name: str = Field(min_length=1, max_length=256)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("workspace name cannot be blank")
        return normalized


class WorkspaceResponse(ApiModel):
    id: UUID
    name: str
    created_at: datetime


class TaskCreateRequest(ApiModel):
    session_id: UUID
    goal: str = Field(min_length=1, max_length=100_000)
    acceptance: AcceptanceSpec | None = None
    provider: str | None = Field(default=None, min_length=1, max_length=64)
    model: str | None = Field(default=None, min_length=1, max_length=256)
    run_mode: RunMode = RunMode.RETRIEVAL
    project_id: UUID | None = None
    # F-02：显式指定的输入文件（授权项目根内的相对路径）；创建时冻结内容哈希。
    input_paths: list[str] = Field(default_factory=list, max_length=64)

    @field_validator("run_mode")
    @classmethod
    def public_run_mode(cls, value: RunMode) -> RunMode:
        if value is RunMode.PINNED_SKILL:
            raise ValueError("pinned_skill mode is reserved for evaluation")
        return value

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
    run_mode: str
    created_at: datetime
    updated_at: datetime


class TaskResponse(ApiModel):
    id: UUID
    session_id: UUID
    project_id: UUID | None = None
    goal: str
    acceptance: AcceptanceSpec | None
    frozen_inputs: dict[str, Any] | None = None
    status: TaskStatus
    cancel_requested: bool
    attempt_count: int
    created_at: datetime
    updated_at: datetime
    latest_run: RunResponse


class InstructionCreateRequest(ApiModel):
    """运行中补充的约束；不支持在终止态追加。"""

    content: str = Field(min_length=1, max_length=20_000)

    @field_validator("content")
    @classmethod
    def normalize_content(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("补充内容不能为空")
        return normalized


class InstructionResponse(ApiModel):
    id: UUID
    task_id: UUID
    content: str
    session_sequence: int
    created_at: datetime
    # 非空表示它已经进入模型上下文；页面用它显示"已接受/已注入"。
    injected_at: datetime | None = None


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
