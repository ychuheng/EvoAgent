"""EvoAgent PostgreSQL 持久化记录。"""

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
    event,
    inspect,
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
from evoagent.skills.lifecycle import SkillStatus, SkillVersionStatus
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
    pinned_skill_version_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("skill_versions.id", ondelete="RESTRICT")
    )
    config_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSON)
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
    __table_args__ = (CheckConstraint("size_bytes >= 0", name="size_bytes_non_negative"),)

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    run_id: Mapped[UUID] = mapped_column(ForeignKey("runs.id", ondelete="RESTRICT"), index=True)
    type: Mapped[str] = mapped_column(String(64))
    uri: Mapped[str] = mapped_column(String(2_048))
    content_hash: Mapped[str] = mapped_column(String(128))
    size_bytes: Mapped[int] = mapped_column(Integer)
    attributes: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class SkillRecord(Base):
    __tablename__ = "skills"
    __table_args__ = (CheckConstraint("lock_version >= 0", name="lock_version_non_negative"),)

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(128))
    slug: Mapped[str] = mapped_column(String(64), unique=True)
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
        UniqueConstraint("name", "version"),
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint(
            "length(content_hash) = 71 AND content_hash LIKE 'sha256:%'",
            name="content_hash_format",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
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

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
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


class EvalRunRecord(Base):
    __tablename__ = "eval_runs"
    __table_args__ = (
        UniqueConstraint("experiment_id", "eval_case_id", "mode", "repeat_index"),
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
    __table_args__ = (UniqueConstraint("skill_version_id", "source_run_id"),)

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    skill_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("skill_versions.id", ondelete="RESTRICT"), index=True
    )
    source_run_id: Mapped[UUID] = mapped_column(ForeignKey("runs.id", ondelete="RESTRICT"))
    source_eval_run_id: Mapped[UUID] = mapped_column(
        ForeignKey("eval_runs.id", ondelete="RESTRICT")
    )
    trace_artifact_id: Mapped[UUID] = mapped_column(ForeignKey("artifacts.id", ondelete="RESTRICT"))
    source_trace_hash: Mapped[str] = mapped_column(String(71))


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
    mode: Mapped[str] = mapped_column(String(32))
    rank: Mapped[int] = mapped_column(Integer)
    score: Mapped[float] = mapped_column()
    query_terms: Mapped[list[str]] = mapped_column(JSON)
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


def _reject_changed_fields(record: object, field_names: tuple[str, ...]) -> None:
    state = inspect(record)
    changed = [name for name in field_names if state.attrs[name].history.has_changes()]
    if changed:
        raise ValueError(f"immutable record fields cannot change: {', '.join(changed)}")


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

    _reject_changed_fields(record, ("name", "version", "content_hash", "created_at"))


@event.listens_for(EvalCaseRecord, "before_update")
def protect_eval_case(_mapper: object, _connection: object, _record: EvalCaseRecord) -> None:
    """Case 随数据集版本创建后不可原地修改。"""

    raise ValueError("eval cases are immutable; create a new dataset version")


@event.listens_for(SkillSourceRecord, "before_update")
def protect_skill_source(_mapper: object, _connection: object, _record: SkillSourceRecord) -> None:
    """来源血缘创建后不可被改写。"""

    raise ValueError("skill sources are immutable")
