"""阶段二 PostgreSQL 持久化记录。"""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from evoagent.db.base import Base
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


class SessionRecord(Base):
    __tablename__ = "sessions"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    title: Mapped[str] = mapped_column(String(256))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class MessageRecord(Base):
    __tablename__ = "messages"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    session_id: Mapped[UUID] = mapped_column(
        ForeignKey("sessions.id", ondelete="RESTRICT"), index=True
    )
    role: Mapped[str] = mapped_column(String(32))
    content: Mapped[str] = mapped_column(Text)
    token_count: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class TaskRecord(Base):
    __tablename__ = "tasks"
    __table_args__ = (
        CheckConstraint("attempt_count >= 0", name="attempt_count_non_negative"),
        CheckConstraint("lock_version >= 0", name="lock_version_non_negative"),
        Index("ix_tasks_claim", "status", "next_attempt_at", "lease_expires_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    session_id: Mapped[UUID] = mapped_column(
        ForeignKey("sessions.id", ondelete="RESTRICT"), index=True
    )
    goal: Mapped[str] = mapped_column(Text)
    status: Mapped[TaskStatus] = mapped_column(
        enum_column(TaskStatus, "task_status"), default=TaskStatus.CREATED
    )
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
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
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    task_id: Mapped[UUID] = mapped_column(ForeignKey("tasks.id", ondelete="RESTRICT"), index=True)
    status: Mapped[PersistentRunStatus] = mapped_column(
        enum_column(PersistentRunStatus, "run_status"), default=PersistentRunStatus.QUEUED
    )
    provider: Mapped[str] = mapped_column(String(64))
    model: Mapped[str] = mapped_column(String(256))
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
        CheckConstraint("sequence >= 1", name="sequence_positive"),
        CheckConstraint("schema_version >= 1", name="schema_version_positive"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    run_id: Mapped[UUID] = mapped_column(ForeignKey("runs.id", ondelete="RESTRICT"), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    event_type: Mapped[str] = mapped_column(String(128), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    schema_version: Mapped[int] = mapped_column(Integer, default=1)
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


class ArtifactRecord(Base):
    __tablename__ = "artifacts"
    __table_args__ = (CheckConstraint("size_bytes >= 0", name="size_bytes_non_negative"),)

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    run_id: Mapped[UUID] = mapped_column(ForeignKey("runs.id", ondelete="RESTRICT"), index=True)
    type: Mapped[str] = mapped_column(String(64))
    uri: Mapped[str] = mapped_column(String(2_048))
    content_hash: Mapped[str] = mapped_column(String(128))
    size_bytes: Mapped[int] = mapped_column(Integer)
    attributes: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
