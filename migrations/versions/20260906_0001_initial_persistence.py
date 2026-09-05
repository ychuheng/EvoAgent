"""建立阶段二初始持久化结构。

Revision ID: 20260906_0001
Revises:
Create Date: 2026-09-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260906_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """创建任务、运行、事件、快照与副作用相关表。"""

    uuid = sa.Uuid()
    timestamp = sa.DateTime(timezone=True)
    task_status = sa.Enum(
        "created",
        "queued",
        "running",
        "waiting_tool",
        "waiting_user",
        "retrying",
        "paused",
        "recovering",
        "completed",
        "failed",
        "cancelled",
        name="task_status",
        native_enum=False,
    )
    run_status = sa.Enum(
        "queued",
        "running",
        "waiting_user",
        "retrying",
        "paused",
        "recovering",
        "completed",
        "failed",
        "cancelled",
        "timeout",
        "limit_reached",
        name="run_status",
        native_enum=False,
    )
    tool_call_status = sa.Enum(
        "pending",
        "running",
        "succeeded",
        "failed",
        "denied",
        "unknown",
        name="tool_call_status",
        native_enum=False,
    )
    tool_effect_status = sa.Enum(
        "prepared",
        "executing",
        "committed",
        "failed",
        "unknown",
        name="tool_effect_status",
        native_enum=False,
    )
    approval_status = sa.Enum(
        "pending",
        "approved",
        "rejected",
        "cancelled",
        name="approval_status",
        native_enum=False,
    )
    op.create_table(
        "sessions",
        sa.Column("id", uuid, nullable=False),
        sa.Column("title", sa.String(256), nullable=False),
        sa.Column("created_at", timestamp, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_sessions"),
    )
    op.create_table(
        "messages",
        sa.Column("id", uuid, nullable=False),
        sa.Column("session_id", uuid, nullable=False),
        sa.Column("role", sa.String(32), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=True),
        sa.Column("created_at", timestamp, nullable=False),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["sessions.id"],
            name="fk_messages_session_id_sessions",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_messages"),
    )
    op.create_index("ix_messages_session_id", "messages", ["session_id"])
    op.create_table(
        "tasks",
        sa.Column("id", uuid, nullable=False),
        sa.Column("session_id", uuid, nullable=False),
        sa.Column("goal", sa.Text(), nullable=False),
        sa.Column("status", task_status, nullable=False),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False),
        sa.Column("lease_owner", sa.String(128), nullable=True),
        sa.Column("lease_expires_at", timestamp, nullable=True),
        sa.Column("heartbeat_at", timestamp, nullable=True),
        sa.Column("next_attempt_at", timestamp, nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("lock_version", sa.Integer(), nullable=False),
        sa.Column("created_at", timestamp, nullable=False),
        sa.Column("updated_at", timestamp, nullable=False),
        sa.CheckConstraint("attempt_count >= 0", name="ck_tasks_attempt_count_non_negative"),
        sa.CheckConstraint("lock_version >= 0", name="ck_tasks_lock_version_non_negative"),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["sessions.id"],
            name="fk_tasks_session_id_sessions",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_tasks"),
    )
    op.create_index("ix_tasks_session_id", "tasks", ["session_id"])
    op.create_index("ix_tasks_claim", "tasks", ["status", "next_attempt_at", "lease_expires_at"])
    op.create_table(
        "runs",
        sa.Column("id", uuid, nullable=False),
        sa.Column("task_id", uuid, nullable=False),
        sa.Column("status", run_status, nullable=False),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("model", sa.String(256), nullable=False),
        sa.Column("next_event_sequence", sa.Integer(), nullable=False),
        sa.Column("lock_version", sa.Integer(), nullable=False),
        sa.Column("final_answer", sa.Text(), nullable=True),
        sa.Column("error_code", sa.String(128), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", timestamp, nullable=True),
        sa.Column("ended_at", timestamp, nullable=True),
        sa.Column("created_at", timestamp, nullable=False),
        sa.Column("updated_at", timestamp, nullable=False),
        sa.CheckConstraint("next_event_sequence >= 1", name="ck_runs_next_event_sequence_positive"),
        sa.CheckConstraint("lock_version >= 0", name="ck_runs_lock_version_non_negative"),
        sa.ForeignKeyConstraint(
            ["task_id"], ["tasks.id"], name="fk_runs_task_id_tasks", ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_runs"),
    )
    op.create_index("ix_runs_task_id", "runs", ["task_id"])
    op.create_table(
        "turns",
        sa.Column("id", uuid, nullable=False),
        sa.Column("run_id", uuid, nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("request_summary", sa.JSON(), nullable=False),
        sa.Column("response_summary", sa.JSON(), nullable=True),
        sa.Column("usage", sa.JSON(), nullable=True),
        sa.Column("created_at", timestamp, nullable=False),
        sa.CheckConstraint("sequence >= 1", name="ck_turns_sequence_positive"),
        sa.ForeignKeyConstraint(
            ["run_id"], ["runs.id"], name="fk_turns_run_id_runs", ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_turns"),
        sa.UniqueConstraint("run_id", "sequence", name="uq_turns_run_id"),
    )
    op.create_table(
        "tool_calls",
        sa.Column("id", uuid, nullable=False),
        sa.Column("run_id", uuid, nullable=False),
        sa.Column("turn_id", uuid, nullable=False),
        sa.Column("provider_call_id", sa.String(256), nullable=False),
        sa.Column("tool_name", sa.String(64), nullable=False),
        sa.Column("arguments", sa.JSON(), nullable=False),
        sa.Column("risk", sa.String(2), nullable=False),
        sa.Column("status", tool_call_status, nullable=False),
        sa.Column("result_summary", sa.Text(), nullable=True),
        sa.Column("error_code", sa.String(128), nullable=True),
        sa.Column("created_at", timestamp, nullable=False),
        sa.Column("updated_at", timestamp, nullable=False),
        sa.ForeignKeyConstraint(
            ["run_id"], ["runs.id"], name="fk_tool_calls_run_id_runs", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["turn_id"], ["turns.id"], name="fk_tool_calls_turn_id_turns", ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_tool_calls"),
        sa.UniqueConstraint("run_id", "provider_call_id", name="uq_tool_calls_run_id"),
    )
    op.create_index("ix_tool_calls_run_id", "tool_calls", ["run_id"])
    op.create_table(
        "run_events",
        sa.Column("id", uuid, nullable=False),
        sa.Column("run_id", uuid, nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(128), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("created_at", timestamp, nullable=False),
        sa.CheckConstraint("sequence >= 1", name="ck_run_events_sequence_positive"),
        sa.CheckConstraint("schema_version >= 1", name="ck_run_events_schema_version_positive"),
        sa.ForeignKeyConstraint(
            ["run_id"], ["runs.id"], name="fk_run_events_run_id_runs", ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_run_events"),
        sa.UniqueConstraint("run_id", "sequence", name="uq_run_events_run_id"),
    )
    op.create_index("ix_run_events_run_id", "run_events", ["run_id"])
    op.create_index("ix_run_events_event_type", "run_events", ["event_type"])
    op.create_table(
        "run_snapshots",
        sa.Column("id", uuid, nullable=False),
        sa.Column("run_id", uuid, nullable=False),
        sa.Column("event_sequence", sa.Integer(), nullable=False),
        sa.Column("state", sa.JSON(), nullable=False),
        sa.Column("context_ref", sa.String(1024), nullable=True),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("created_at", timestamp, nullable=False),
        sa.CheckConstraint("event_sequence >= 1", name="ck_run_snapshots_event_sequence_positive"),
        sa.CheckConstraint("schema_version >= 1", name="ck_run_snapshots_schema_version_positive"),
        sa.ForeignKeyConstraint(
            ["run_id"], ["runs.id"], name="fk_run_snapshots_run_id_runs", ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_run_snapshots"),
        sa.UniqueConstraint("run_id", "event_sequence", name="uq_run_snapshots_run_id"),
    )
    op.create_index("ix_run_snapshots_run_id", "run_snapshots", ["run_id"])
    op.create_table(
        "tool_effects",
        sa.Column("id", uuid, nullable=False),
        sa.Column("tool_call_id", uuid, nullable=False),
        sa.Column("effect_scope", sa.String(256), nullable=False),
        sa.Column("semantic_key", sa.String(128), nullable=False),
        sa.Column("status", tool_effect_status, nullable=False),
        sa.Column("result_hash", sa.String(128), nullable=True),
        sa.Column("committed_at", timestamp, nullable=True),
        sa.Column("created_at", timestamp, nullable=False),
        sa.ForeignKeyConstraint(
            ["tool_call_id"],
            ["tool_calls.id"],
            name="fk_tool_effects_tool_call_id_tool_calls",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_tool_effects"),
        sa.UniqueConstraint("effect_scope", "semantic_key", name="uq_tool_effects_effect_scope"),
        sa.UniqueConstraint("tool_call_id", name="uq_tool_effects_tool_call_id"),
    )
    op.create_table(
        "tool_approvals",
        sa.Column("id", uuid, nullable=False),
        sa.Column("task_id", uuid, nullable=False),
        sa.Column("tool_call_id", uuid, nullable=False),
        sa.Column("status", approval_status, nullable=False),
        sa.Column("risk", sa.String(2), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("requested_at", timestamp, nullable=False),
        sa.Column("decided_at", timestamp, nullable=True),
        sa.ForeignKeyConstraint(
            ["task_id"], ["tasks.id"], name="fk_tool_approvals_task_id_tasks", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["tool_call_id"],
            ["tool_calls.id"],
            name="fk_tool_approvals_tool_call_id_tool_calls",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_tool_approvals"),
        sa.UniqueConstraint("tool_call_id", name="uq_tool_approvals_tool_call_id"),
    )
    op.create_index("ix_tool_approvals_task_id", "tool_approvals", ["task_id"])
    op.create_table(
        "artifacts",
        sa.Column("id", uuid, nullable=False),
        sa.Column("run_id", uuid, nullable=False),
        sa.Column("type", sa.String(64), nullable=False),
        sa.Column("uri", sa.String(2048), nullable=False),
        sa.Column("content_hash", sa.String(128), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", timestamp, nullable=False),
        sa.CheckConstraint("size_bytes >= 0", name="ck_artifacts_size_bytes_non_negative"),
        sa.ForeignKeyConstraint(
            ["run_id"], ["runs.id"], name="fk_artifacts_run_id_runs", ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_artifacts"),
    )
    op.create_index("ix_artifacts_run_id", "artifacts", ["run_id"])


def downgrade() -> None:
    """按外键依赖的反向顺序删除全部阶段二表。"""

    op.drop_index("ix_artifacts_run_id", table_name="artifacts")
    op.drop_table("artifacts")
    op.drop_index("ix_tool_approvals_task_id", table_name="tool_approvals")
    op.drop_table("tool_approvals")
    op.drop_table("tool_effects")
    op.drop_index("ix_run_snapshots_run_id", table_name="run_snapshots")
    op.drop_table("run_snapshots")
    op.drop_index("ix_run_events_event_type", table_name="run_events")
    op.drop_index("ix_run_events_run_id", table_name="run_events")
    op.drop_table("run_events")
    op.drop_index("ix_tool_calls_run_id", table_name="tool_calls")
    op.drop_table("tool_calls")
    op.drop_table("turns")
    op.drop_index("ix_runs_task_id", table_name="runs")
    op.drop_table("runs")
    op.drop_index("ix_tasks_claim", table_name="tasks")
    op.drop_index("ix_tasks_session_id", table_name="tasks")
    op.drop_table("tasks")
    op.drop_index("ix_messages_session_id", table_name="messages")
    op.drop_table("messages")
    op.drop_table("sessions")
