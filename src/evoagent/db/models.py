"""EvoAgent PostgreSQL 持久化记录。"""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    DDL,
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    event,
    inspect,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from evoagent.db.base import Base
from evoagent.evals.lifecycle import (
    DatasetStatus,
    EvalExperimentKind,
    EvalExperimentStatus,
    EvalRunMode,
    EvalSplit,
    PromotionAction,
)
from evoagent.projects import ProjectAuthorization, ProjectStatus
from evoagent.skills.lifecycle import SkillStatus, SkillVersionStatus
from evoagent.tasks.cancel_notifications import FUNCTION_SQL, TRIGGER_SQL
from evoagent.tasks.state_machine import PersistentRunStatus, TaskStatus


def utc_now() -> datetime:
    """生成带 UTC 时区的时间，避免本地时区进入数据库。"""

    return datetime.now(UTC)


def enum_column(enum_class: type[StrEnum], name: str) -> Enum:
    """让不同数据库都保存 StrEnum 的 value，而不是成员名称。"""

    return Enum(
        enum_class,
        name=name,
        native_enum=False,
        values_callable=lambda members: [member.value for member in members],
        validate_strings=True,
    )


class ToolCallStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    DENIED = "denied"
    UNKNOWN = "unknown"


class ToolEffectStatus(StrEnum):
    PREPARED = "prepared"
    EXECUTING = "executing"
    COMMITTED = "committed"
    FAILED = "failed"
    UNKNOWN = "unknown"


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


DEFAULT_WORKSPACE_ID = UUID("00000000-0000-0000-0000-000000000001")


@event.listens_for(Base.metadata, "before_create")
def enable_vector_extension(_target, connection, **_kwargs):
    if connection.dialect.name == "postgresql":
        connection.exec_driver_sql("CREATE EXTENSION IF NOT EXISTS vector")


class WorkspaceRecord(Base):
    __tablename__ = "workspaces"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(256))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


@event.listens_for(WorkspaceRecord.__table__, "after_create")
def seed_local_workspace(_target, connection, **_kwargs):
    connection.execute(
        WorkspaceRecord.__table__.insert().values(
            id=DEFAULT_WORKSPACE_ID, name="Local workspace", created_at=utc_now()
        )
    )


class SessionRecord(Base):
    __tablename__ = "sessions"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    title: Mapped[str] = mapped_column(String(256))
    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="RESTRICT"), default=DEFAULT_WORKSPACE_ID
    )
    project_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("projects.id", ondelete="RESTRICT"), index=True
    )
    next_message_sequence: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ProjectRecord(Base):
    """用户显式授权的项目根。

    `root` 是登记时规范化的物理路径；`authorization_version` 每次授权变化自增，
    运行中的 Task 靠它判断"我拿到的授权是否还是当前授权"（撤销后下一次调用即被拒绝）。
    """

    __tablename__ = "projects"
    __table_args__ = (
        CheckConstraint("authorization_version >= 1", name="authorization_version_positive"),
        CheckConstraint("length(root) <= 2048", name="root_length"),
        Index("ix_projects_root", "root", unique=True),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(256))
    root: Mapped[str] = mapped_column(String(2048))
    authorization: Mapped[ProjectAuthorization] = mapped_column(
        enum_column(ProjectAuthorization, "project_authorization"),
        default=ProjectAuthorization.READ,
    )
    status: Mapped[ProjectStatus] = mapped_column(
        enum_column(ProjectStatus, "project_status"), default=ProjectStatus.AVAILABLE
    )
    next_event_sequence: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    authorization_version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="RESTRICT"), default=DEFAULT_WORKSPACE_ID
    )
    lock_version: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class ProjectEventRecord(Base):
    """项目授权审计：登记、改级、撤销都留一行，不可改写。"""

    __tablename__ = "project_events"
    __table_args__ = (
        UniqueConstraint("project_id", "sequence"),
        CheckConstraint("sequence >= 1", name="sequence_positive"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="RESTRICT"), index=True
    )
    sequence: Mapped[int] = mapped_column(Integer)
    event_type: Mapped[str] = mapped_column(String(64))
    authorization: Mapped[ProjectAuthorization] = mapped_column(
        enum_column(ProjectAuthorization, "project_event_authorization")
    )
    authorization_version: Mapped[int] = mapped_column(Integer)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


@event.listens_for(ProjectEventRecord, "before_update")
def protect_project_event(_mapper, _connection, _record):
    """审计行写入后不可改写。"""

    raise ValueError("project events are append-only")


class MessageRecord(Base):
    __tablename__ = "messages"
    __table_args__ = (
        UniqueConstraint("session_id", "session_sequence"),
        UniqueConstraint("run_id", "kind"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    session_id: Mapped[UUID] = mapped_column(
        ForeignKey("sessions.id", ondelete="RESTRICT"), index=True
    )
    role: Mapped[str] = mapped_column(String(32))
    content: Mapped[str] = mapped_column(Text)
    token_count: Mapped[int | None] = mapped_column(Integer)
    task_id: Mapped[UUID | None] = mapped_column(ForeignKey("tasks.id", ondelete="RESTRICT"))
    run_id: Mapped[UUID | None] = mapped_column(ForeignKey("runs.id", ondelete="RESTRICT"))
    session_sequence: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(32), default="legacy")
    content_hash: Mapped[str] = mapped_column(String(71))
    backfill: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    # I-03：运行中补充的指令在下一个安全边界注入；注入时间非空表示它已经进入模型上下文，
    # 因此之后不会重复注入，也不会篡改已经执行或审批中的动作。
    injected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class TaskRecord(Base):
    __tablename__ = "tasks"
    __table_args__ = (
        CheckConstraint(
            "family IS NULL OR family IN "
            "('general','coding','research','document','data','file_management')",
            name="family_valid",
        ),
        CheckConstraint("attempt_count >= 0", name="attempt_count_non_negative"),
        CheckConstraint("lock_version >= 0", name="lock_version_non_negative"),
        Index("ix_tasks_claim", "status", "next_attempt_at", "lease_expires_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    session_id: Mapped[UUID] = mapped_column(
        ForeignKey("sessions.id", ondelete="RESTRICT"), index=True
    )
    # Task 创建时冻结项目绑定（含授权版本）；之后切换页面项目不影响在跑 Task。
    project_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("projects.id", ondelete="RESTRICT"), index=True
    )
    project_authorization_version: Mapped[int | None] = mapped_column(Integer)
    family: Mapped[str | None] = mapped_column(String(32))
    goal: Mapped[str] = mapped_column(Text)
    acceptance: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    # F-02：创建时冻结的输入集（路径 + 内容哈希 + 类型）；运行中变化即按输入变化终止。
    frozen_inputs: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    status: Mapped[TaskStatus] = mapped_column(
        enum_column(TaskStatus, "task_status"), default=TaskStatus.CREATED
    )
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    history_before_sequence: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    lease_epoch: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    lease_owner: Mapped[str | None] = mapped_column(String(128))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    lock_version: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class RunRecord(Base):
    __tablename__ = "runs"
    __table_args__ = (
        CheckConstraint("next_event_sequence >= 1", name="next_event_sequence_positive"),
        CheckConstraint("lock_version >= 0", name="lock_version_non_negative"),
        CheckConstraint(
            "data_role IN ('personal','dev','train','holdout','runtime_eval','legacy')",
            name="data_role_valid",
        ),
        CheckConstraint(
            "run_mode != 'pinned_skill' OR pinned_skill_version_id IS NOT NULL",
            name="pinned_skill_present",
        ),
        CheckConstraint(
            "run_mode IN ('baseline', 'retrieval', 'pinned_skill')",
            name="run_mode_valid",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    task_id: Mapped[UUID] = mapped_column(ForeignKey("tasks.id", ondelete="RESTRICT"), index=True)
    status: Mapped[PersistentRunStatus] = mapped_column(
        enum_column(PersistentRunStatus, "run_status"), default=PersistentRunStatus.QUEUED
    )
    provider: Mapped[str] = mapped_column(String(64))
    model: Mapped[str] = mapped_column(String(256))
    run_mode: Mapped[str] = mapped_column(String(32), default="retrieval")
    data_role: Mapped[str] = mapped_column(String(32), default="legacy", server_default="legacy")
    next_feedback_revision: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    next_context_revision: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    pinned_skill_version_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("skill_versions.id", ondelete="RESTRICT")
    )
    skill_selection_frozen: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    config_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    tool_catalog_snapshot: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON)
    config_hash: Mapped[str | None] = mapped_column(String(71))
    next_event_sequence: Mapped[int] = mapped_column(Integer, default=1)
    lock_version: Mapped[int] = mapped_column(Integer, default=0)
    final_answer: Mapped[str | None] = mapped_column(Text)
    error_code: Mapped[str | None] = mapped_column(String(128))
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class SpendRecord(Base):
    """M0c 付费账本：逐次记录 token 用量与费用（微元），按 scope 分账。

    只追加、不覆盖：报告可以按 scope 与 Task 求和核对，避免"只报告便宜的成功样本"。
    """

    __tablename__ = "spend_records"
    __table_args__ = (
        CheckConstraint("input_tokens >= 0", name="input_tokens_non_negative"),
        CheckConstraint("output_tokens >= 0", name="output_tokens_non_negative"),
        CheckConstraint("cost_micros >= 0", name="cost_micros_non_negative"),
        Index("ix_spend_scope_created", "scope", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    purpose: Mapped[str] = mapped_column(String(32), default="legacy", server_default="legacy")
    learning_request_id: Mapped[UUID | None] = mapped_column(ForeignKey("learning_requests.id"))
    reservation_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("learning_spend_reservations.id"), unique=True
    )
    scope: Mapped[str] = mapped_column(String(32))
    task_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("tasks.id", ondelete="RESTRICT"), index=True
    )
    run_id: Mapped[UUID | None] = mapped_column(Uuid)
    provider: Mapped[str] = mapped_column(String(64))
    model: Mapped[str] = mapped_column(String(256))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_micros: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    input_price_micros_per_million: Mapped[int | None] = mapped_column(Integer)
    output_price_micros_per_million: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


@event.listens_for(SpendRecord, "before_update")
def protect_spend_record(_mapper, _connection, _record):
    """付费账本写入后不可改写。"""

    raise ValueError("spend records are append-only")


class TurnRecord(Base):
    __tablename__ = "turns"
    __table_args__ = (
        UniqueConstraint("run_id", "sequence"),
        CheckConstraint("sequence >= 1", name="sequence_positive"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    run_id: Mapped[UUID] = mapped_column(ForeignKey("runs.id", ondelete="RESTRICT"))
    sequence: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32))
    request_summary: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    response_summary: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    usage: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ToolCallRecord(Base):
    __tablename__ = "tool_calls"
    __table_args__ = (UniqueConstraint("run_id", "provider_call_id"),)

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    run_id: Mapped[UUID] = mapped_column(ForeignKey("runs.id", ondelete="RESTRICT"), index=True)
    turn_id: Mapped[UUID] = mapped_column(ForeignKey("turns.id", ondelete="RESTRICT"))
    provider_call_id: Mapped[str] = mapped_column(String(256))
    tool_name: Mapped[str] = mapped_column(String(64))
    arguments: Mapped[dict[str, Any]] = mapped_column(JSON)
    execution_binding: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    risk: Mapped[str] = mapped_column(String(2))
    status: Mapped[ToolCallStatus] = mapped_column(
        enum_column(ToolCallStatus, "tool_call_status"), default=ToolCallStatus.PENDING
    )
    result_summary: Mapped[str | None] = mapped_column(Text)
    error_code: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class RunEventRecord(Base):
    __tablename__ = "run_events"
    __table_args__ = (
        UniqueConstraint("run_id", "sequence"),
        UniqueConstraint("run_id", "dedupe_key", name="uq_run_events_dedupe_key"),
        CheckConstraint("sequence >= 1", name="sequence_positive"),
        CheckConstraint("schema_version >= 1", name="schema_version_positive"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    run_id: Mapped[UUID] = mapped_column(ForeignKey("runs.id", ondelete="RESTRICT"), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    event_type: Mapped[str] = mapped_column(String(128), index=True)
    dedupe_key: Mapped[str | None] = mapped_column(String(71), nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    schema_version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class RunFeedbackRecord(Base):
    __tablename__ = "run_feedback"
    __table_args__ = (
        UniqueConstraint("run_id", "revision", name="uq_run_feedback_revision"),
        UniqueConstraint("run_id", "client_request_id", name="uq_run_feedback_client_request"),
        CheckConstraint("revision >= 1 AND learning_revision >= 1", name="revisions_positive"),
        CheckConstraint("intent IN ('method','fact','unsure','mixed')", name="intent_valid"),
        CheckConstraint("verdict IN ('helpful','needs_fix','incorrect')", name="verdict_valid"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    run_id: Mapped[UUID] = mapped_column(ForeignKey("runs.id", ondelete="RESTRICT"), index=True)
    revision: Mapped[int] = mapped_column(Integer)
    learning_revision: Mapped[int] = mapped_column(Integer)
    learning_payload_hash: Mapped[str] = mapped_column(String(71))
    request_body_hash: Mapped[str] = mapped_column(String(71))
    intent: Mapped[str] = mapped_column(String(32))
    learn_from_feedback: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    client_request_id: Mapped[str] = mapped_column(String(128))
    verdict: Mapped[str] = mapped_column(String(32))
    comment: Mapped[str] = mapped_column(Text, default="")
    correction: Mapped[str] = mapped_column(Text, default="")
    evidence_refs: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    supersedes_id: Mapped[UUID | None] = mapped_column(ForeignKey("run_feedback.id"))
    actor_id: Mapped[str] = mapped_column(String(128), default="human")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class LearningPolicyRecord(Base):
    __tablename__ = "learning_policies"
    __table_args__ = (
        CheckConstraint("mode IN ('off','manual','suggest')", name="mode_valid"),
        CheckConstraint(
            "daily_limit_micros IS NULL OR daily_limit_micros >= 0", name="daily_budget_valid"
        ),
        CheckConstraint(
            "request_limit_micros IS NULL OR request_limit_micros >= 0", name="request_budget_valid"
        ),
    )
    workspace_id: Mapped[UUID] = mapped_column(ForeignKey("workspaces.id"), primary_key=True)
    mode: Mapped[str] = mapped_column(String(32), default="off")
    daily_limit_micros: Mapped[int | None] = mapped_column(Integer)
    request_limit_micros: Mapped[int | None] = mapped_column(Integer)
    daily_candidate_limit: Mapped[int] = mapped_column(Integer, default=3)
    cooldown_seconds: Mapped[int] = mapped_column(Integer, default=86400)
    max_source_risk: Mapped[str] = mapped_column(String(8), default="R1")
    lock_version: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class LearningRequestRecord(Base):
    __tablename__ = "learning_requests"
    __table_args__ = (
        UniqueConstraint("workspace_id", "source_key", name="uq_learning_requests_source_key"),
        UniqueConstraint(
            "workspace_id", "client_request_id", name="uq_learning_requests_client_request"
        ),
        CheckConstraint(
            "(request_kind = 'propose' AND source_key LIKE 'propose:v1:%') OR "
            "(request_kind = 'validate' AND source_key LIKE 'validate:v1:%')",
            name="identity_namespace",
        ),
        Index("ix_learning_requests_scope_status", "workspace_id", "status", "created_at"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    request_kind: Mapped[str] = mapped_column(String(16))
    parent_request_id: Mapped[UUID | None] = mapped_column(ForeignKey("learning_requests.id"))
    workspace_id: Mapped[UUID] = mapped_column(ForeignKey("workspaces.id"))
    project_id: Mapped[UUID | None] = mapped_column(ForeignKey("projects.id"))
    origin_run_id: Mapped[UUID] = mapped_column(ForeignKey("runs.id"))
    trigger: Mapped[str] = mapped_column(String(32))
    source_key: Mapped[str] = mapped_column(String(160))
    client_request_id: Mapped[str] = mapped_column(String(128))
    request_body_hash: Mapped[str] = mapped_column(String(71))
    feedback_revision: Mapped[int] = mapped_column(Integer, default=0)
    target_skill_id: Mapped[UUID | None] = mapped_column(ForeignKey("skills.id"))
    base_version_id: Mapped[UUID | None] = mapped_column(ForeignKey("skill_versions.id"))
    status: Mapped[str] = mapped_column(String(32), default="queued")
    stage: Mapped[str] = mapped_column(String(32), default="prepare")
    policy_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON)
    policy_hash: Mapped[str] = mapped_column(String(71))
    frozen_inputs: Mapped[dict[str, Any]] = mapped_column(JSON)
    candidate_version_id: Mapped[UUID | None] = mapped_column(ForeignKey("skill_versions.id"))
    validation_experiment_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(
            "eval_experiments.id",
            name="fk_learning_requests_validation_experiment_id_eval_experiments",
            use_alter=True,
        )
    )
    validation_report: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    validation_report_hash: Mapped[str | None] = mapped_column(String(71))
    error_code: Mapped[str | None] = mapped_column(String(128))
    lock_version: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class ValidationJudgmentRecord(Base):
    """Append-only human judgments bound to actual validation evidence."""

    __tablename__ = "validation_judgments"
    __table_args__ = (
        UniqueConstraint("workspace_id", "client_request_id", name="uq_validation_judgment_client"),
        UniqueConstraint("request_id", "revision", name="uq_validation_judgment_revision"),
        CheckConstraint("revision > 0", name="revision_positive"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(ForeignKey("workspaces.id"))
    request_id: Mapped[UUID] = mapped_column(ForeignKey("learning_requests.id"))
    revision: Mapped[int] = mapped_column(Integer)
    client_request_id: Mapped[str] = mapped_column(String(128))
    request_body_hash: Mapped[str] = mapped_column(String(71))
    actor: Mapped[str] = mapped_column(String(128))
    base_report_hash: Mapped[str] = mapped_column(String(71))
    judgments: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    resulting_report: Mapped[dict[str, Any]] = mapped_column(JSON)
    resulting_report_hash: Mapped[str] = mapped_column(String(71))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ValidationReplicaBindingRecord(Base):
    """Immutable host-created initial input identity for one validation arm/run."""

    __tablename__ = "validation_replica_bindings"
    __table_args__ = (
        UniqueConstraint("request_id", "case_key", "arm", "repeat_index"),
        CheckConstraint("arm IN ('control','treatment')", name="replica_arm_valid"),
        CheckConstraint("repeat_index >= 0 AND repeat_index < 3", name="replica_repeat_valid"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="RESTRICT"))
    request_id: Mapped[UUID] = mapped_column(
        ForeignKey("learning_requests.id", ondelete="RESTRICT")
    )
    eval_run_id: Mapped[UUID] = mapped_column(
        ForeignKey("eval_runs.id", ondelete="RESTRICT"), unique=True
    )
    run_id: Mapped[UUID] = mapped_column(ForeignKey("runs.id", ondelete="RESTRICT"), unique=True)
    case_key: Mapped[str] = mapped_column(String(64))
    arm: Mapped[str] = mapped_column(String(16))
    repeat_index: Mapped[int] = mapped_column(Integer)
    fixture_id: Mapped[str] = mapped_column(String(64))
    input_fingerprint: Mapped[str] = mapped_column(String(71))
    manifest: Mapped[dict[str, Any]] = mapped_column(JSON)
    manifest_hash: Mapped[str] = mapped_column(String(71))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class LearningRequestAliasRecord(Base):
    """同一学习语义的多个客户端幂等键都必须保留，不能仅记住首个键。"""

    __tablename__ = "learning_request_aliases"
    workspace_id: Mapped[UUID] = mapped_column(ForeignKey("workspaces.id"), primary_key=True)
    client_request_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    request_id: Mapped[UUID] = mapped_column(ForeignKey("learning_requests.id"))
    request_body_hash: Mapped[str] = mapped_column(String(71))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class LearningSourceRecord(Base):
    __tablename__ = "learning_sources"
    __table_args__ = (UniqueConstraint("run_id", "source_revision"),)
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    run_id: Mapped[UUID] = mapped_column(ForeignKey("runs.id"), index=True)
    feedback_id: Mapped[UUID | None] = mapped_column(ForeignKey("run_feedback.id"))
    source_revision: Mapped[str] = mapped_column(String(71))
    source_role: Mapped[str] = mapped_column(String(32))
    parent_skill_versions: Mapped[list[str]] = mapped_column(JSON, default=list)
    evidence_manifest: Mapped[dict[str, Any]] = mapped_column(JSON)
    artifact_id: Mapped[UUID] = mapped_column(ForeignKey("artifacts.id"))
    content_hash: Mapped[str] = mapped_column(String(71))
    status: Mapped[str] = mapped_column(String(32), default="valid")
    revocation_epoch: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class LearningSpendReservationRecord(Base):
    __tablename__ = "learning_spend_reservations"
    __table_args__ = (
        CheckConstraint("reserved_micros >= 0", name="reserved_nonnegative"),
        CheckConstraint(
            "status IN ('reserved','settled','unknown','released')", name="status_valid"
        ),
        Index("ix_learning_spend_day", "workspace_id", "budget_day", "status"),
        CheckConstraint(
            "(dispatcher_kind = 'maintenance' AND task_id IS NULL AND run_id IS NULL "
            "AND task_epoch IS NULL) OR (dispatcher_kind = 'task' AND task_id IS NOT NULL "
            "AND run_id IS NOT NULL AND task_epoch IS NOT NULL AND job_id IS NULL "
            "AND job_epoch IS NULL AND request_body_hash IS NOT NULL)",
            name="learning_dispatch_identity",
        ),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(ForeignKey("workspaces.id"))
    request_id: Mapped[UUID] = mapped_column(ForeignKey("learning_requests.id"))
    call_key: Mapped[str] = mapped_column(String(160), unique=True)
    budget_day: Mapped[str] = mapped_column(String(10))
    reserved_micros: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32), default="reserved")
    actual_micros: Mapped[int | None] = mapped_column(Integer)
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    job_id: Mapped[UUID | None] = mapped_column(ForeignKey("maintenance_jobs.id"))
    job_epoch: Mapped[int | None] = mapped_column(Integer)
    dispatcher_kind: Mapped[str] = mapped_column(
        String(16), default="maintenance", server_default="maintenance"
    )
    task_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("tasks.id", name="fk_learning_spend_task")
    )
    run_id: Mapped[UUID | None] = mapped_column(ForeignKey("runs.id", name="fk_learning_spend_run"))
    task_epoch: Mapped[int | None] = mapped_column(Integer)
    request_body_hash: Mapped[str | None] = mapped_column(String(71))
    scope: Mapped[str] = mapped_column(String(32), default="legacy", server_default="legacy")
    input_price_micros_per_million: Mapped[int | None] = mapped_column(Integer)
    output_price_micros_per_million: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class RunSnapshotRecord(Base):
    __tablename__ = "run_snapshots"
    __table_args__ = (
        UniqueConstraint("run_id", "event_sequence"),
        CheckConstraint("event_sequence >= 1", name="event_sequence_positive"),
        CheckConstraint("schema_version >= 1", name="schema_version_positive"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    run_id: Mapped[UUID] = mapped_column(ForeignKey("runs.id", ondelete="RESTRICT"), index=True)
    event_sequence: Mapped[int] = mapped_column(Integer)
    state: Mapped[dict[str, Any]] = mapped_column(JSON)
    context_ref: Mapped[str | None] = mapped_column(String(1_024))
    schema_version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ContextRevisionRecord(Base):
    __tablename__ = "context_revisions"
    __table_args__ = (
        UniqueConstraint("run_id", "revision", name="uq_context_revision_number"),
        UniqueConstraint("run_id", "dedupe_key", name="uq_context_revision_dedupe"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    run_id: Mapped[UUID] = mapped_column(ForeignKey("runs.id", ondelete="RESTRICT"))
    revision: Mapped[int] = mapped_column(Integer)
    parent_id: Mapped[UUID | None] = mapped_column(ForeignKey("context_revisions.id"))
    dedupe_key: Mapped[str] = mapped_column(String(71))
    input_hash: Mapped[str] = mapped_column(String(71))
    policy_hash: Mapped[str] = mapped_column(String(71))
    artifact_id: Mapped[UUID] = mapped_column(ForeignKey("artifacts.id", ondelete="RESTRICT"))
    summary: Mapped[dict[str, Any]] = mapped_column(JSON)
    estimate: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class MemoryEntryRecord(Base):
    __tablename__ = "memory_entries"
    __table_args__ = (
        UniqueConstraint("workspace_id", "scope_key", "fact_key"),
        CheckConstraint(
            "(scope_key = 'workspace' AND session_id IS NULL) OR "
            "(scope_key <> 'workspace' AND session_id IS NOT NULL)",
            name="memory_scope",
        ),
        ForeignKeyConstraint(
            ["id", "current_version_id"],
            ["memory_versions.entry_id", "memory_versions.id"],
            use_alter=True,
            name="fk_memory_current_version",
        ),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="RESTRICT"))
    session_id: Mapped[UUID | None] = mapped_column(ForeignKey("sessions.id", ondelete="RESTRICT"))
    scope_key: Mapped[str] = mapped_column(String(64))
    fact_key: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(32), default="proposed")
    current_version_id: Mapped[UUID | None] = mapped_column(Uuid)
    lock_version: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class MemoryVersionRecord(Base):
    __tablename__ = "memory_versions"
    __table_args__ = (
        UniqueConstraint("entry_id", "revision", name="uq_memory_revision"),
        UniqueConstraint("entry_id", "id", name="uq_memory_entry_version"),
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="memory_confidence"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    entry_id: Mapped[UUID] = mapped_column(ForeignKey("memory_entries.id", ondelete="RESTRICT"))
    revision: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(32))
    content: Mapped[str | None] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(71))
    status: Mapped[str] = mapped_column(String(32), default="proposed")
    confidence: Mapped[float] = mapped_column(default=0.5)
    confidence_method: Mapped[str] = mapped_column(String(128))
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    supersedes_version_id: Mapped[UUID | None] = mapped_column(ForeignKey("memory_versions.id"))
    origin_type: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class MemorySourceRecord(Base):
    __tablename__ = "memory_sources"
    __table_args__ = (UniqueConstraint("version_id", "message_id"),)
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    version_id: Mapped[UUID] = mapped_column(ForeignKey("memory_versions.id", ondelete="RESTRICT"))
    message_id: Mapped[UUID] = mapped_column(ForeignKey("messages.id", ondelete="RESTRICT"))
    source_hash: Mapped[str] = mapped_column(String(71))
    locator: Mapped[str] = mapped_column(String(128))


class MemoryEventRecord(Base):
    __tablename__ = "memory_events"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    entry_id: Mapped[UUID] = mapped_column(ForeignKey("memory_entries.id", ondelete="RESTRICT"))
    version_id: Mapped[UUID | None] = mapped_column(ForeignKey("memory_versions.id"))
    action: Mapped[str] = mapped_column(String(32))
    actor: Mapped[str] = mapped_column(String(128))
    reason: Mapped[str] = mapped_column(String(256))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class SessionArchiveRecord(Base):
    __tablename__ = "session_archives"
    __table_args__ = (
        UniqueConstraint("session_id", "start_sequence", "end_sequence", "source_hash"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    session_id: Mapped[UUID] = mapped_column(ForeignKey("sessions.id", ondelete="RESTRICT"))
    start_sequence: Mapped[int] = mapped_column(Integer)
    end_sequence: Mapped[int] = mapped_column(Integer)
    source_hash: Mapped[str] = mapped_column(String(71))
    summary: Mapped[str | None] = mapped_column(Text)
    config: Mapped[dict[str, Any]] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(32), default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class RunMemoryReferenceRecord(Base):
    __tablename__ = "run_memory_references"
    __table_args__ = (UniqueConstraint("run_id", "version_id"),)
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    run_id: Mapped[UUID] = mapped_column(ForeignKey("runs.id", ondelete="RESTRICT"))
    version_id: Mapped[UUID] = mapped_column(ForeignKey("memory_versions.id", ondelete="RESTRICT"))
    content_hash: Mapped[str] = mapped_column(String(71))


class MaintenanceJobRecord(Base):
    __tablename__ = "maintenance_jobs"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    dedupe_key: Mapped[str] = mapped_column(String(256), unique=True)
    kind: Mapped[str] = mapped_column(String(32))
    priority: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    learning_request_id: Mapped[UUID | None] = mapped_column(ForeignKey("learning_requests.id"))
    result_schema_version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(32), default="pending")
    lease_owner: Mapped[str | None] = mapped_column(String(128))
    lease_epoch: Mapped[int] = mapped_column(Integer, default=0)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    error_code: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


@event.listens_for(MemoryVersionRecord, "before_update")
def protect_memory_content(_mapper, _connection, record):
    for field in (
        "entry_id",
        "revision",
        "kind",
        "content_hash",
        "confidence",
        "confidence_method",
        "valid_from",
        "expires_at",
        "origin_type",
        "supersedes_version_id",
    ):
        if inspect(record).attrs[field].history.has_changes():
            raise ValueError("memory version identity is immutable")
    if inspect(record).attrs.content.history.has_changes() and (
        record.content is not None or record.status != "erased"
    ):
        raise ValueError("memory content is immutable except explicit erase")


class EmbeddingProfileRecord(Base):
    __tablename__ = "embedding_profiles"
    __table_args__ = (CheckConstraint("dimension BETWEEN 1 AND 2000", name="supported_dimension"),)
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    model: Mapped[str] = mapped_column(String(256), unique=True)
    dimension: Mapped[int] = mapped_column(Integer, default=1536)
    metric: Mapped[str] = mapped_column(String(32), default="cosine")
    preprocessing: Mapped[str] = mapped_column(String(64), default="text-v1")
    active_generation: Mapped[int] = mapped_column(Integer, default=0)
    next_generation: Mapped[int] = mapped_column(Integer, default=1)


class IndexGenerationRecord(Base):
    __tablename__ = "index_generations"
    __table_args__ = (UniqueConstraint("profile_id", "generation"),)
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    profile_id: Mapped[UUID] = mapped_column(ForeignKey("embedding_profiles.id"))
    generation: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32), default="building")
    manifest: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class RetrievalDocumentRecord(Base):
    __tablename__ = "retrieval_documents"
    __table_args__ = (
        UniqueConstraint("source_key"),
        CheckConstraint(
            "(CASE WHEN skill_version_id IS NULL THEN 0 ELSE 1 END + "
            "CASE WHEN memory_version_id IS NULL THEN 0 ELSE 1 END + "
            "CASE WHEN archive_id IS NULL THEN 0 ELSE 1 END) = 1",
            name="one_source",
        ),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    source_key: Mapped[str] = mapped_column(String(128))
    skill_version_id: Mapped[UUID | None] = mapped_column(ForeignKey("skill_versions.id"))
    memory_version_id: Mapped[UUID | None] = mapped_column(ForeignKey("memory_versions.id"))
    archive_id: Mapped[UUID | None] = mapped_column(ForeignKey("session_archives.id"))
    workspace_id: Mapped[UUID | None] = mapped_column(ForeignKey("workspaces.id"))
    session_id: Mapped[UUID | None] = mapped_column(ForeignKey("sessions.id"))
    input_hash: Mapped[str] = mapped_column(String(71))
    source_hash: Mapped[str] = mapped_column(String(71))
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class DocumentEmbeddingRecord(Base):
    __tablename__ = "document_embeddings"
    __table_args__ = (UniqueConstraint("document_id", "profile_id", "generation", "input_hash"),)
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    document_id: Mapped[UUID] = mapped_column(ForeignKey("retrieval_documents.id"))
    profile_id: Mapped[UUID] = mapped_column(ForeignKey("embedding_profiles.id"))
    generation: Mapped[int] = mapped_column(Integer)
    input_hash: Mapped[str] = mapped_column(String(71))
    from evoagent.retrieval.vector import VECTOR

    vector: Mapped[list[float]] = mapped_column(VECTOR)


class RetrievalBatchRecord(Base):
    __tablename__ = "retrieval_batches"
    __table_args__ = (UniqueConstraint("run_id", "purpose"),)
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    run_id: Mapped[UUID] = mapped_column(ForeignKey("runs.id"))
    purpose: Mapped[str] = mapped_column(String(32), default="context")
    query_hash: Mapped[str] = mapped_column(String(71))
    workspace_id: Mapped[UUID] = mapped_column(ForeignKey("workspaces.id"))
    session_id: Mapped[UUID] = mapped_column(ForeignKey("sessions.id"))
    profile_id: Mapped[UUID | None] = mapped_column(ForeignKey("embedding_profiles.id"))
    generation: Mapped[int | None] = mapped_column(Integer)
    config: Mapped[dict[str, Any]] = mapped_column(JSON)
    degraded: Mapped[str | None] = mapped_column(String(128))
    selected_count: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class RetrievalSelectionRecord(Base):
    __tablename__ = "retrieval_selections"
    __table_args__ = (UniqueConstraint("batch_id", "source_key"),)
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    batch_id: Mapped[UUID] = mapped_column(ForeignKey("retrieval_batches.id"))
    source_key: Mapped[str] = mapped_column(String(128))
    source_hash: Mapped[str] = mapped_column(String(71))
    text_hash: Mapped[str] = mapped_column(String(71))
    text: Mapped[str | None] = mapped_column(Text)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSON)
    rank: Mapped[int | None] = mapped_column(Integer)
    omission_reason: Mapped[str | None] = mapped_column(String(64))


class ToolEffectRecord(Base):
    __tablename__ = "tool_effects"
    __table_args__ = (UniqueConstraint("effect_scope", "semantic_key"),)

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    tool_call_id: Mapped[UUID] = mapped_column(
        ForeignKey("tool_calls.id", ondelete="RESTRICT"), unique=True
    )
    effect_scope: Mapped[str] = mapped_column(String(256))
    semantic_key: Mapped[str] = mapped_column(String(128))
    status: Mapped[ToolEffectStatus] = mapped_column(
        enum_column(ToolEffectStatus, "tool_effect_status"), default=ToolEffectStatus.PREPARED
    )
    result_hash: Mapped[str | None] = mapped_column(String(128))
    result_content: Mapped[str | None] = mapped_column(Text)
    committed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ToolApprovalRecord(Base):
    __tablename__ = "tool_approvals"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    task_id: Mapped[UUID] = mapped_column(ForeignKey("tasks.id", ondelete="RESTRICT"), index=True)
    tool_call_id: Mapped[UUID] = mapped_column(
        ForeignKey("tool_calls.id", ondelete="RESTRICT"), unique=True
    )
    status: Mapped[ApprovalStatus] = mapped_column(
        enum_column(ApprovalStatus, "approval_status"), default=ApprovalStatus.PENDING
    )
    risk: Mapped[str] = mapped_column(String(2))
    reason: Mapped[str] = mapped_column(Text)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    response: Mapped[str | None] = mapped_column(Text)


class ArtifactRecord(Base):
    __tablename__ = "artifacts"
    __table_args__ = (
        CheckConstraint("size_bytes >= 0", name="size_bytes_non_negative"),
        # 注入门禁的复用前提：只有"当前策略版本 + 当前正文 hash 都记全了"才允许标
        # verified。缺任一字段的 verified 是自相矛盾的，直接在库层面拒绝。
        CheckConstraint(
            "redaction_status <> 'verified' OR "
            "(redaction_policy_version IS NOT NULL AND redaction_checked_hash IS NOT NULL)",
            name="redaction_verified_requires_metadata",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    run_id: Mapped[UUID] = mapped_column(ForeignKey("runs.id", ondelete="RESTRICT"), index=True)
    type: Mapped[str] = mapped_column(String(64))
    uri: Mapped[str] = mapped_column(String(2_048))
    content_hash: Mapped[str] = mapped_column(String(128))
    size_bytes: Mapped[int] = mapped_column(Integer)
    attributes: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    # M-A0 注入门禁的检查元数据。**不做回填**：字段为空表示"未按当前策略检查过"，
    # 旧行保持 NULL/unchecked，绝不批量标成 verified（那是伪造检查结论）。
    redaction_policy_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    redaction_checked_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    redaction_status: Mapped[str] = mapped_column(String(16), nullable=False, default="unchecked")


class SkillRecord(Base):
    __tablename__ = "skills"
    __table_args__ = (
        CheckConstraint("lock_version >= 0", name="lock_version_non_negative"),
        UniqueConstraint("workspace_id", "slug", name="uq_skills_workspace_slug"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(128))
    slug: Mapped[str] = mapped_column(String(64))
    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id"), default=DEFAULT_WORKSPACE_ID
    )
    project_id: Mapped[UUID | None] = mapped_column(ForeignKey("projects.id"))
    superseded_by_skill_id: Mapped[UUID | None] = mapped_column(ForeignKey("skills.id"))
    next_version_number: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    next_event_sequence: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    description: Mapped[str] = mapped_column(Text)
    status: Mapped[SkillStatus] = mapped_column(
        enum_column(SkillStatus, "skill_status"), default=SkillStatus.ENABLED
    )
    active_version_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(
            "skill_versions.id",
            ondelete="RESTRICT",
            use_alter=True,
            name="fk_skills_active_version_id_skill_versions",
        )
    )
    lock_version: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class SkillVersionRecord(Base):
    __tablename__ = "skill_versions"
    __table_args__ = (
        UniqueConstraint("skill_id", "version"),
        UniqueConstraint("id", "skill_id", name="uq_skill_versions_id_skill"),
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint("schema_version >= 1", name="schema_version_positive"),
        CheckConstraint(
            "length(content_hash) = 71 AND content_hash LIKE 'sha256:%'",
            name="content_hash_format",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    skill_id: Mapped[UUID] = mapped_column(ForeignKey("skills.id", ondelete="RESTRICT"), index=True)
    parent_version_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("skill_versions.id", ondelete="RESTRICT")
    )
    version: Mapped[int] = mapped_column(Integer)
    schema_version: Mapped[int] = mapped_column(Integer)
    definition: Mapped[dict[str, Any]] = mapped_column(JSON)
    content_hash: Mapped[str] = mapped_column(String(71))
    extraction_key: Mapped[str] = mapped_column(String(71), unique=True)
    lifecycle_status: Mapped[SkillVersionStatus] = mapped_column(
        enum_column(SkillVersionStatus, "skill_version_status"),
        default=SkillVersionStatus.DRAFT,
    )
    evaluation_report_hash: Mapped[str | None] = mapped_column(String(71))
    gate_report_hash: Mapped[str | None] = mapped_column(String(71))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class EvalDatasetRecord(Base):
    __tablename__ = "eval_datasets"
    __table_args__ = (
        CheckConstraint("purpose IN ('formal','personal_dev')", name="purpose_valid"),
        UniqueConstraint("name", "version"),
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint(
            "length(content_hash) = 71 AND content_hash LIKE 'sha256:%'",
            name="content_hash_format",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    purpose: Mapped[str] = mapped_column(String(32), default="formal", server_default="formal")
    name: Mapped[str] = mapped_column(String(128))
    version: Mapped[int] = mapped_column(Integer)
    content_hash: Mapped[str] = mapped_column(String(71))
    status: Mapped[DatasetStatus] = mapped_column(
        enum_column(DatasetStatus, "dataset_status"), default=DatasetStatus.DRAFT
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class EvalCaseRecord(Base):
    __tablename__ = "eval_cases"
    __table_args__ = (UniqueConstraint("dataset_id", "case_key"),)

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    dataset_id: Mapped[UUID] = mapped_column(
        ForeignKey("eval_datasets.id", ondelete="RESTRICT"), index=True
    )
    case_key: Mapped[str] = mapped_column(String(128))
    task_family: Mapped[str] = mapped_column(String(128), index=True)
    split: Mapped[EvalSplit] = mapped_column(enum_column(EvalSplit, "eval_split"))
    public_input: Mapped[dict[str, Any]] = mapped_column(JSON)
    private_validators: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    risk_profile: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class EvalExperimentRecord(Base):
    __tablename__ = "eval_experiments"
    __table_args__ = (
        CheckConstraint("purpose IN ('formal','personal_validation')", name="purpose_valid"),
    )
    lease_epoch: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    purpose: Mapped[str] = mapped_column(String(32), default="formal", server_default="formal")
    comparison_version_id: Mapped[UUID | None] = mapped_column(ForeignKey("skill_versions.id"))
    learning_request_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(
            "learning_requests.id", use_alter=True, name="fk_eval_experiments_learning_request"
        ),
        unique=True,
    )
    kind: Mapped[EvalExperimentKind] = mapped_column(
        enum_column(EvalExperimentKind, "eval_experiment_kind")
    )
    skill_version_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("skill_versions.id", ondelete="RESTRICT")
    )
    dataset_id: Mapped[UUID] = mapped_column(
        ForeignKey("eval_datasets.id", ondelete="RESTRICT"), index=True
    )
    status: Mapped[EvalExperimentStatus] = mapped_column(
        enum_column(EvalExperimentStatus, "eval_experiment_status"),
        default=EvalExperimentStatus.QUEUED,
    )
    config_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    config_hash: Mapped[str] = mapped_column(String(71))
    report_artifact_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("artifacts.id", ondelete="RESTRICT")
    )
    report_hash: Mapped[str | None] = mapped_column(String(71))
    gate_report: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    gate_report_hash: Mapped[str | None] = mapped_column(String(71))
    lease_owner: Mapped[str | None] = mapped_column(String(128))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class RuntimeExperimentRecord(Base):
    __tablename__ = "runtime_experiments"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    dataset_id: Mapped[UUID] = mapped_column(ForeignKey("eval_datasets.id"))
    spec: Mapped[dict[str, Any]] = mapped_column(JSON)
    spec_hash: Mapped[str] = mapped_column(String(71))
    status: Mapped[str] = mapped_column(String(32), default="queued")
    report: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    report_hash: Mapped[str | None] = mapped_column(String(71))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class RuntimeEvalRunRecord(Base):
    __tablename__ = "runtime_eval_runs"
    __table_args__ = (UniqueConstraint("experiment_id", "case_id", "arm", "repeat_index"),)
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    experiment_id: Mapped[UUID] = mapped_column(ForeignKey("runtime_experiments.id"), index=True)
    case_id: Mapped[UUID] = mapped_column(ForeignKey("eval_cases.id"))
    arm: Mapped[str] = mapped_column(String(16))
    repeat_index: Mapped[int] = mapped_column(Integer)
    run_id: Mapped[UUID] = mapped_column(ForeignKey("runs.id"), unique=True)
    metrics: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    validation_results: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON)


class EvalRunRecord(Base):
    __tablename__ = "eval_runs"
    __table_args__ = (
        UniqueConstraint("experiment_id", "eval_case_id", "arm", "repeat_index"),
        CheckConstraint("arm IN ('control','treatment')", name="arm_valid"),
        CheckConstraint("repeat_index >= 0", name="repeat_index_non_negative"),
        CheckConstraint(
            "(mode = 'baseline' AND skill_version_id IS NULL) OR "
            "(mode = 'pinned_skill' AND skill_version_id IS NOT NULL)",
            name="mode_skill_consistent",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    experiment_id: Mapped[UUID] = mapped_column(
        ForeignKey("eval_experiments.id", ondelete="RESTRICT"), index=True
    )
    eval_case_id: Mapped[UUID] = mapped_column(
        ForeignKey("eval_cases.id", ondelete="RESTRICT"), index=True
    )
    arm: Mapped[str] = mapped_column(
        String(16),
        default=lambda context: (
            "control"
            if str(context.get_current_parameters().get("mode")) == "baseline"
            else "treatment"
        ),
    )
    mode: Mapped[EvalRunMode] = mapped_column(enum_column(EvalRunMode, "eval_run_mode"))
    repeat_index: Mapped[int] = mapped_column(Integer, default=0)
    task_id: Mapped[UUID] = mapped_column(ForeignKey("tasks.id", ondelete="RESTRICT"))
    run_id: Mapped[UUID] = mapped_column(ForeignKey("runs.id", ondelete="RESTRICT"), unique=True)
    skill_version_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("skill_versions.id", ondelete="RESTRICT")
    )
    paired_eval_run_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("eval_runs.id", ondelete="RESTRICT")
    )
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    validation_results: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    passed: Mapped[bool] = mapped_column(Boolean)
    comparable: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class SkillSourceRecord(Base):
    __tablename__ = "skill_sources"
    __table_args__ = (
        UniqueConstraint("skill_version_id", "source_run_id"),
        CheckConstraint(
            (
                "(source_kind = 'train_eval' AND source_eval_run_id IS NOT NULL AND "
                "learning_source_id IS NULL) OR "
                "(source_kind = 'personal' AND source_eval_run_id IS NULL AND "
                "learning_source_id IS NOT NULL)"
            ),
            name="source_kind_exclusive",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    skill_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("skill_versions.id", ondelete="RESTRICT"), index=True
    )
    source_run_id: Mapped[UUID] = mapped_column(ForeignKey("runs.id", ondelete="RESTRICT"))
    source_kind: Mapped[str] = mapped_column(
        String(32), default="train_eval", server_default="train_eval"
    )
    learning_source_id: Mapped[UUID | None] = mapped_column(ForeignKey("learning_sources.id"))
    source_eval_run_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("eval_runs.id", ondelete="RESTRICT")
    )
    trace_artifact_id: Mapped[UUID] = mapped_column(ForeignKey("artifacts.id", ondelete="RESTRICT"))
    source_trace_hash: Mapped[str] = mapped_column(String(71))


class SkillTrialRecord(Base):
    __tablename__ = "skill_trials"
    __table_args__ = (
        CheckConstraint("status IN ('active','suspended','replaced')", name="status_valid"),
        CheckConstraint("lock_version >= 0", name="lock_version_non_negative"),
        Index(
            "uq_skill_trials_active_scope",
            "skill_id",
            "scope_key",
            unique=True,
            postgresql_where=text("status = 'active'"),
            sqlite_where=text("status = 'active'"),
        ),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    skill_id: Mapped[UUID] = mapped_column(ForeignKey("skills.id", ondelete="RESTRICT"), index=True)
    version_id: Mapped[UUID] = mapped_column(ForeignKey("skill_versions.id", ondelete="RESTRICT"))
    workspace_id: Mapped[UUID] = mapped_column(ForeignKey("workspaces.id"))
    project_id: Mapped[UUID | None] = mapped_column(ForeignKey("projects.id"))
    scope_key: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(16), default="active")
    validation_request_id: Mapped[UUID] = mapped_column(ForeignKey("learning_requests.id"))
    report_hash: Mapped[str] = mapped_column(String(71))
    health_policy_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON)
    health_policy_hash: Mapped[str] = mapped_column(String(71))
    suspended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    suspension_reason: Mapped[str | None] = mapped_column(String(1000))
    reviewer: Mapped[str] = mapped_column(String(128))
    reason: Mapped[str] = mapped_column(String(1000))
    lock_version: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class RunSkillSelectionRecord(Base):
    __tablename__ = "run_skill_selections"
    __table_args__ = (
        UniqueConstraint("run_id", "rank"),
        CheckConstraint("rank >= 1", name="rank_positive"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    run_id: Mapped[UUID] = mapped_column(ForeignKey("runs.id", ondelete="RESTRICT"), index=True)
    skill_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("skill_versions.id", ondelete="RESTRICT")
    )
    trial_id: Mapped[UUID | None] = mapped_column(ForeignKey("skill_trials.id"))
    origin: Mapped[str] = mapped_column(String(16), default="legacy", server_default="legacy")
    scope_key: Mapped[str | None] = mapped_column(String(128))
    rendered_hash: Mapped[str | None] = mapped_column(String(71))
    mode: Mapped[str] = mapped_column(String(32))
    rank: Mapped[int] = mapped_column(Integer)
    score: Mapped[float] = mapped_column()
    query_terms: Mapped[list[str]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class SkillObservationRecord(Base):
    __tablename__ = "skill_observations"
    __table_args__ = (
        UniqueConstraint("run_id", "version_id", "feedback_revision"),
        CheckConstraint("feedback_revision >= 0", name="feedback_revision_non_negative"),
        CheckConstraint(
            "outcome IN ('verified_success','verified_failure','unknown')", name="outcome_valid"
        ),
        CheckConstraint(
            "attribution IN ('skill_related','environment','user_request','uncertain')",
            name="attribution_valid",
        ),
        Index("ix_skill_observations_trial_finished", "trial_id", "first_finished_at"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    run_id: Mapped[UUID] = mapped_column(ForeignKey("runs.id"))
    version_id: Mapped[UUID] = mapped_column(ForeignKey("skill_versions.id"))
    trial_id: Mapped[UUID | None] = mapped_column(ForeignKey("skill_trials.id"))
    selection_id: Mapped[UUID] = mapped_column(ForeignKey("run_skill_selections.id"))
    feedback_revision: Mapped[int] = mapped_column(Integer, default=0)
    outcome: Mapped[str] = mapped_column(String(32))
    attribution: Mapped[str] = mapped_column(String(32))
    evidence: Mapped[dict[str, Any]] = mapped_column(JSON)
    first_finished_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    input_fingerprint: Mapped[str] = mapped_column(String(71))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class PromotionDecisionRecord(Base):
    __tablename__ = "promotion_decisions"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    skill_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("skill_versions.id", ondelete="RESTRICT"), index=True
    )
    action: Mapped[PromotionAction] = mapped_column(
        enum_column(PromotionAction, "promotion_action")
    )
    reviewer: Mapped[str] = mapped_column(String(128))
    reason: Mapped[str] = mapped_column(Text)
    gate_report_hash: Mapped[str | None] = mapped_column(String(71))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class SkillEventRecord(Base):
    __tablename__ = "skill_events"
    __table_args__ = (
        UniqueConstraint("skill_id", "sequence"),
        CheckConstraint("sequence >= 1", name="sequence_positive"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    skill_id: Mapped[UUID] = mapped_column(ForeignKey("skills.id", ondelete="RESTRICT"), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    event_type: Mapped[str] = mapped_column(String(128))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class MCPServerRecord(Base):
    __tablename__ = "mcp_servers"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    config: Mapped[dict[str, Any]] = mapped_column(JSON)
    lock_version: Mapped[int] = mapped_column(Integer, default=0)
    latest_revision: Mapped[int] = mapped_column(Integer, default=0)
    execution_state: Mapped[str] = mapped_column(
        String(16), default="disabled", server_default="disabled"
    )
    active_catalog_id: Mapped[UUID | None] = mapped_column(Uuid)
    execution_version: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class MCPCatalogRecord(Base):
    __tablename__ = "mcp_catalogs"
    __table_args__ = (UniqueConstraint("server_id", "revision"),)
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    server_id: Mapped[UUID] = mapped_column(ForeignKey("mcp_servers.id", ondelete="RESTRICT"))
    revision: Mapped[int] = mapped_column(Integer)
    config_version: Mapped[int] = mapped_column(Integer)
    content_hash: Mapped[str] = mapped_column(String(71))
    protocol_version: Mapped[str] = mapped_column(String(32))
    server_info: Mapped[dict[str, Any]] = mapped_column(JSON)
    capabilities: Mapped[dict[str, Any]] = mapped_column(JSON)
    tools: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    diff: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class MCPToolReviewRecord(Base):
    __tablename__ = "mcp_tool_reviews"
    __table_args__ = (UniqueConstraint("catalog_id", "tool_name", "lock_version"),)
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    catalog_id: Mapped[UUID] = mapped_column(ForeignKey("mcp_catalogs.id", ondelete="RESTRICT"))
    tool_name: Mapped[str] = mapped_column(String(128))
    lock_version: Mapped[int] = mapped_column(Integer, default=0)
    approved: Mapped[bool] = mapped_column(Boolean, default=False)
    risk: Mapped[str] = mapped_column(String(8), default="R3")
    effect: Mapped[str] = mapped_column(String(32), default="non_idempotent_write")
    reviewer: Mapped[str | None] = mapped_column(String(128))
    reason: Mapped[str] = mapped_column(Text, default="awaiting local review")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class MCPHealthRecord(Base):
    __tablename__ = "mcp_health"
    __table_args__ = (UniqueConstraint("server_id", "instance_id"),)
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    server_id: Mapped[UUID] = mapped_column(ForeignKey("mcp_servers.id", ondelete="RESTRICT"))
    instance_id: Mapped[str] = mapped_column(String(128))
    config_version: Mapped[int] = mapped_column(Integer)
    state: Mapped[str] = mapped_column(String(32))
    error_code: Mapped[str | None] = mapped_column(String(128))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


@event.listens_for(MCPCatalogRecord, "before_update")
@event.listens_for(MCPToolReviewRecord, "before_update")
def protect_mcp_evidence(_mapper, _connection, _record):
    raise ValueError("MCP catalog and review evidence is immutable")


def _reject_changed_fields(record: object, field_names: tuple[str, ...]) -> None:
    state = inspect(record)
    changed = [name for name in field_names if state.attrs[name].history.has_changes()]
    if changed:
        raise ValueError(f"immutable record fields cannot change: {', '.join(changed)}")


@event.listens_for(EmbeddingProfileRecord, "before_update")
def protect_embedding_identity(_mapper, _connection, record):
    _reject_changed_fields(record, ("model", "dimension", "metric", "preprocessing"))


@event.listens_for(SkillVersionRecord, "before_update")
def protect_skill_version_body(
    _mapper: object, _connection: object, record: SkillVersionRecord
) -> None:
    """版本创建后只允许生命周期状态变化，正文和来源身份保持不变。"""

    _reject_changed_fields(
        record,
        (
            "skill_id",
            "parent_version_id",
            "version",
            "schema_version",
            "definition",
            "content_hash",
            "extraction_key",
            "created_at",
        ),
    )
    state = inspect(record)
    for name in ("evaluation_report_hash", "gate_report_hash"):
        history = state.attrs[name].history
        if history.has_changes() and any(value is not None for value in history.deleted):
            raise ValueError(f"immutable report hash cannot be replaced: {name}")


@event.listens_for(EvalExperimentRecord, "before_update")
def protect_experiment_reports(
    _mapper: object, _connection: object, record: EvalExperimentRecord
) -> None:
    """实验身份与配置不可修改，报告首次写入后也不可替换。"""

    _reject_changed_fields(
        record,
        (
            "kind",
            "purpose",
            "comparison_version_id",
            "learning_request_id",
            "skill_version_id",
            "dataset_id",
            "config_snapshot",
            "config_hash",
            "created_at",
        ),
    )

    state = inspect(record)
    for name in (
        "report_artifact_id",
        "report_hash",
        "gate_report",
        "gate_report_hash",
    ):
        history = state.attrs[name].history
        if history.has_changes() and any(value is not None for value in history.deleted):
            raise ValueError(f"immutable experiment report cannot be replaced: {name}")


@event.listens_for(EvalDatasetRecord, "before_update")
def protect_dataset_version(
    _mapper: object, _connection: object, record: EvalDatasetRecord
) -> None:
    """数据集版本只能改变生命周期状态，内容变化必须创建新版本。"""

    _reject_changed_fields(record, ("purpose", "name", "version", "content_hash", "created_at"))


@event.listens_for(EvalCaseRecord, "before_update")
def protect_eval_case(_mapper: object, _connection: object, _record: EvalCaseRecord) -> None:
    """Case 随数据集版本创建后不可原地修改。"""

    raise ValueError("eval cases are immutable; create a new dataset version")


@event.listens_for(SkillSourceRecord, "before_update")
def protect_skill_source(_mapper: object, _connection: object, _record: SkillSourceRecord) -> None:
    """来源血缘创建后不可被改写。"""

    raise ValueError("skill sources are immutable")


@event.listens_for(RunRecord, "before_update")
def protect_tool_catalog_snapshot(_mapper, _connection, record):
    history = inspect(record).attrs.tool_catalog_snapshot.history
    if history.has_changes() and any(value is not None for value in history.deleted):
        raise ValueError("run tool catalog snapshot is immutable")


class SandboxExecutionRecord(Base):
    __tablename__ = "sandbox_executions"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    run_id: Mapped[UUID | None] = mapped_column(ForeignKey("runs.id", ondelete="RESTRICT"))
    server_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("mcp_servers.id", ondelete="RESTRICT")
    )
    lease_epoch: Mapped[int | None] = mapped_column(Integer)
    profile_hash: Mapped[str] = mapped_column(String(71))
    container_id: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(32), default="starting")
    error_code: Mapped[str | None] = mapped_column(String(128))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


@event.listens_for(RunRecord, "before_update")
def protect_run_data_role(_mapper, _connection, record):
    _reject_changed_fields(record, ("data_role",))


@event.listens_for(RunFeedbackRecord, "before_update")
def protect_feedback(_mapper, _connection, _record):
    raise ValueError("feedback is append-only")


@event.listens_for(LearningRequestRecord, "before_update")
def protect_learning_request_identity(_mapper, _connection, record):
    _reject_changed_fields(
        record,
        (
            "request_kind",
            "parent_request_id",
            "workspace_id",
            "project_id",
            "origin_run_id",
            "trigger",
            "source_key",
            "client_request_id",
            "request_body_hash",
            "feedback_revision",
            "target_skill_id",
            "base_version_id",
            "policy_snapshot",
            "policy_hash",
            "frozen_inputs",
            "created_at",
        ),
    )


@event.listens_for(ValidationJudgmentRecord, "before_update")
def protect_validation_judgment(_mapper, _connection, _record):
    raise ValueError("validation judgments are append-only")


@event.listens_for(ValidationReplicaBindingRecord, "before_update")
@event.listens_for(ValidationReplicaBindingRecord, "before_delete")
def protect_validation_replica_binding(_mapper, _connection, _record):
    raise ValueError("validation replica bindings are immutable")


@event.listens_for(SkillTrialRecord, "before_update")
def protect_trial_identity(_mapper, _connection, record):
    _reject_changed_fields(
        record,
        (
            "skill_id",
            "version_id",
            "workspace_id",
            "project_id",
            "scope_key",
            "validation_request_id",
            "report_hash",
            "health_policy_snapshot",
            "health_policy_hash",
            "reviewer",
            "reason",
            "created_at",
        ),
    )


@event.listens_for(SkillObservationRecord, "before_update")
@event.listens_for(SkillObservationRecord, "before_delete")
def protect_skill_observation(_mapper, _connection, _record):
    raise ValueError("skill observation is append-only")


@event.listens_for(LearningSourceRecord, "before_update")
def protect_learning_source(_mapper, _connection, record):
    _reject_changed_fields(
        record,
        (
            "run_id",
            "feedback_id",
            "source_revision",
            "source_role",
            "parent_skill_versions",
            "evidence_manifest",
            "artifact_id",
            "content_hash",
            "created_at",
        ),
    )


event.listen(
    RunRecord.__table__,
    "after_create",
    DDL(
        "CREATE TRIGGER runs_data_role_immutable BEFORE UPDATE OF data_role ON runs "
        "WHEN NEW.data_role != OLD.data_role BEGIN "
        "SELECT RAISE(ABORT, 'run data_role is immutable'); END"
    ).execute_if(dialect="sqlite"),
)
event.listen(
    RunRecord.__table__,
    "after_create",
    DDL(
        "CREATE OR REPLACE FUNCTION protect_run_data_role() RETURNS trigger LANGUAGE plpgsql AS $$ "
        "BEGIN IF NEW.data_role IS DISTINCT FROM OLD.data_role THEN "
        "RAISE EXCEPTION 'run data_role is immutable'; END IF; RETURN NEW; END $$"
    ).execute_if(dialect="postgresql"),
)
event.listen(
    RunRecord.__table__,
    "after_create",
    DDL(
        "CREATE TRIGGER runs_data_role_immutable BEFORE UPDATE OF data_role ON runs "
        "FOR EACH ROW EXECUTE FUNCTION protect_run_data_role()"
    ).execute_if(dialect="postgresql"),
)
event.listen(
    RunRecord.__table__,
    "after_drop",
    DDL("DROP FUNCTION IF EXISTS protect_run_data_role()").execute_if(dialect="postgresql"),
)

# NOTIFY is delivered only after the cancelling transaction commits. The hint
# carries a UUID, never the goal, tool arguments, credentials or authorization.
event.listen(
    TaskRecord.__table__, "after_create", DDL(FUNCTION_SQL).execute_if(dialect="postgresql")
)
event.listen(
    TaskRecord.__table__, "after_create", DDL(TRIGGER_SQL).execute_if(dialect="postgresql")
)
event.listen(
    TaskRecord.__table__,
    "after_drop",
    DDL("DROP FUNCTION IF EXISTS evoagent_notify_task_cancel()").execute_if(dialect="postgresql"),
)


@event.listens_for(TaskRecord, "before_update")
def protect_task_family(_mapper, _connection, record):
    _reject_changed_fields(record, ("family",))
