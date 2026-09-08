"""建立阶段三 Skill 与 Eval 持久化结构。

Revision ID: 20260908_0003
Revises: 20260908_0002
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260908_0003"
down_revision: str | None = "20260908_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def value_enum(name: str, *values: str) -> sa.Enum:
    return sa.Enum(*values, name=name, native_enum=False)


def upgrade() -> None:
    uuid = sa.Uuid()
    timestamp = sa.DateTime(timezone=True)
    with op.batch_alter_table("runs") as batch:
        batch.add_column(
            sa.Column("run_mode", sa.String(length=32), nullable=False, server_default="retrieval")
        )
        batch.add_column(sa.Column("pinned_skill_version_id", uuid, nullable=True))
        batch.add_column(sa.Column("config_snapshot", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("config_hash", sa.String(length=71), nullable=True))
        batch.create_check_constraint(
            "ck_runs_pinned_skill_present",
            "run_mode != 'pinned_skill' OR pinned_skill_version_id IS NOT NULL",
        )
        batch.create_check_constraint(
            "ck_runs_run_mode_valid",
            "run_mode IN ('baseline', 'retrieval', 'pinned_skill')",
        )

    op.create_table(
        "skills",
        sa.Column("id", uuid, nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("slug", sa.String(length=64), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column(
            "status",
            value_enum("skill_status", "enabled", "disabled", "deprecated"),
            nullable=False,
        ),
        sa.Column("active_version_id", uuid, nullable=True),
        sa.Column("lock_version", sa.Integer(), nullable=False),
        sa.Column("created_at", timestamp, nullable=False),
        sa.Column("updated_at", timestamp, nullable=False),
        sa.CheckConstraint("lock_version >= 0", name="lock_version_non_negative"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug"),
    )
    op.create_table(
        "skill_versions",
        sa.Column("id", uuid, nullable=False),
        sa.Column("skill_id", uuid, nullable=False),
        sa.Column("parent_version_id", uuid, nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("definition", sa.JSON(), nullable=False),
        sa.Column("content_hash", sa.String(length=71), nullable=False),
        sa.Column("extraction_key", sa.String(length=71), nullable=False),
        sa.Column(
            "lifecycle_status",
            value_enum(
                "skill_version_status",
                "draft",
                "evaluating",
                "review_required",
                "active",
                "retired",
                "rejected",
            ),
            nullable=False,
        ),
        sa.Column("created_at", timestamp, nullable=False),
        sa.CheckConstraint("schema_version >= 1", name="schema_version_positive"),
        sa.CheckConstraint("version >= 1", name="version_positive"),
        sa.CheckConstraint(
            "length(content_hash) = 71 AND content_hash LIKE 'sha256:%'",
            name="content_hash_format",
        ),
        sa.ForeignKeyConstraint(["parent_version_id"], ["skill_versions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["skill_id"], ["skills.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("extraction_key"),
        sa.UniqueConstraint("skill_id", "version"),
    )
    op.create_index("ix_skill_versions_skill_id", "skill_versions", ["skill_id"])
    with op.batch_alter_table("skills") as batch:
        batch.create_foreign_key(
            "fk_skills_active_version_id_skill_versions",
            "skill_versions",
            ["active_version_id"],
            ["id"],
            ondelete="RESTRICT",
        )
    with op.batch_alter_table("runs") as batch:
        batch.create_foreign_key(
            "fk_runs_pinned_skill_version_id_skill_versions",
            "skill_versions",
            ["pinned_skill_version_id"],
            ["id"],
            ondelete="RESTRICT",
        )
    op.create_table(
        "eval_datasets",
        sa.Column("id", uuid, nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(length=71), nullable=False),
        sa.Column(
            "status",
            value_enum("dataset_status", "draft", "frozen", "retired"),
            nullable=False,
        ),
        sa.Column("created_at", timestamp, nullable=False),
        sa.CheckConstraint("version >= 1", name="version_positive"),
        sa.CheckConstraint(
            "length(content_hash) = 71 AND content_hash LIKE 'sha256:%'",
            name="content_hash_format",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", "version"),
    )
    op.create_table(
        "eval_cases",
        sa.Column("id", uuid, nullable=False),
        sa.Column("dataset_id", uuid, nullable=False),
        sa.Column("case_key", sa.String(length=128), nullable=False),
        sa.Column("task_family", sa.String(length=128), nullable=False),
        sa.Column("split", value_enum("eval_split", "train", "holdout"), nullable=False),
        sa.Column("public_input", sa.JSON(), nullable=False),
        sa.Column("private_validators", sa.JSON(), nullable=False),
        sa.Column("risk_profile", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["dataset_id"], ["eval_datasets.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("dataset_id", "case_key"),
    )
    op.create_index("ix_eval_cases_dataset_id", "eval_cases", ["dataset_id"])
    op.create_index("ix_eval_cases_task_family", "eval_cases", ["task_family"])
    op.create_table(
        "eval_experiments",
        sa.Column("id", uuid, nullable=False),
        sa.Column(
            "kind",
            value_enum("eval_experiment_kind", "source_validation", "skill_comparison"),
            nullable=False,
        ),
        sa.Column("skill_version_id", uuid, nullable=True),
        sa.Column("dataset_id", uuid, nullable=False),
        sa.Column(
            "status",
            value_enum(
                "eval_experiment_status", "queued", "running", "completed", "failed", "cancelled"
            ),
            nullable=False,
        ),
        sa.Column("config_snapshot", sa.JSON(), nullable=False),
        sa.Column("config_hash", sa.String(length=71), nullable=False),
        sa.Column("gate_report", sa.JSON(), nullable=True),
        sa.Column("lease_owner", sa.String(length=128), nullable=True),
        sa.Column("lease_expires_at", timestamp, nullable=True),
        sa.Column("heartbeat_at", timestamp, nullable=True),
        sa.Column("created_at", timestamp, nullable=False),
        sa.ForeignKeyConstraint(["dataset_id"], ["eval_datasets.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["skill_version_id"], ["skill_versions.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_eval_experiments_dataset_id", "eval_experiments", ["dataset_id"])
    op.create_table(
        "eval_runs",
        sa.Column("id", uuid, nullable=False),
        sa.Column("experiment_id", uuid, nullable=False),
        sa.Column("eval_case_id", uuid, nullable=False),
        sa.Column("mode", value_enum("eval_run_mode", "baseline", "pinned_skill"), nullable=False),
        sa.Column("repeat_index", sa.Integer(), nullable=False),
        sa.Column("task_id", uuid, nullable=False),
        sa.Column("run_id", uuid, nullable=False),
        sa.Column("skill_version_id", uuid, nullable=True),
        sa.Column("paired_eval_run_id", uuid, nullable=True),
        sa.Column("metrics", sa.JSON(), nullable=False),
        sa.Column("validation_results", sa.JSON(), nullable=False),
        sa.Column("passed", sa.Boolean(), nullable=False),
        sa.Column("comparable", sa.Boolean(), nullable=False),
        sa.Column("created_at", timestamp, nullable=False),
        sa.CheckConstraint("repeat_index >= 0", name="repeat_index_non_negative"),
        sa.CheckConstraint(
            "(mode = 'baseline' AND skill_version_id IS NULL) OR "
            "(mode = 'pinned_skill' AND skill_version_id IS NOT NULL)",
            name="mode_skill_consistent",
        ),
        sa.ForeignKeyConstraint(["eval_case_id"], ["eval_cases.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["experiment_id"], ["eval_experiments.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["paired_eval_run_id"], ["eval_runs.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["skill_version_id"], ["skill_versions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("experiment_id", "eval_case_id", "mode", "repeat_index"),
        sa.UniqueConstraint("run_id"),
    )
    op.create_index("ix_eval_runs_eval_case_id", "eval_runs", ["eval_case_id"])
    op.create_index("ix_eval_runs_experiment_id", "eval_runs", ["experiment_id"])
    op.create_table(
        "skill_sources",
        sa.Column("id", uuid, nullable=False),
        sa.Column("skill_version_id", uuid, nullable=False),
        sa.Column("source_run_id", uuid, nullable=False),
        sa.Column("source_eval_run_id", uuid, nullable=False),
        sa.Column("trace_artifact_id", uuid, nullable=False),
        sa.Column("source_trace_hash", sa.String(length=71), nullable=False),
        sa.ForeignKeyConstraint(["skill_version_id"], ["skill_versions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["source_run_id"], ["runs.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["source_eval_run_id"], ["eval_runs.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["trace_artifact_id"], ["artifacts.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("skill_version_id", "source_run_id"),
    )
    op.create_index("ix_skill_sources_skill_version_id", "skill_sources", ["skill_version_id"])
    op.create_table(
        "run_skill_selections",
        sa.Column("id", uuid, nullable=False),
        sa.Column("run_id", uuid, nullable=False),
        sa.Column("skill_version_id", uuid, nullable=False),
        sa.Column("mode", sa.String(length=32), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("query_terms", sa.JSON(), nullable=False),
        sa.Column("created_at", timestamp, nullable=False),
        sa.CheckConstraint("rank >= 1", name="rank_positive"),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["skill_version_id"], ["skill_versions.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "rank"),
    )
    op.create_index("ix_run_skill_selections_run_id", "run_skill_selections", ["run_id"])
    op.create_table(
        "promotion_decisions",
        sa.Column("id", uuid, nullable=False),
        sa.Column("skill_version_id", uuid, nullable=False),
        sa.Column(
            "action",
            value_enum(
                "promotion_action",
                "approve",
                "reject",
                "disable",
                "enable",
                "deprecate",
                "rollback",
            ),
            nullable=False,
        ),
        sa.Column("reviewer", sa.String(length=128), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("gate_report_hash", sa.String(length=71), nullable=True),
        sa.Column("created_at", timestamp, nullable=False),
        sa.ForeignKeyConstraint(["skill_version_id"], ["skill_versions.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_promotion_decisions_skill_version_id", "promotion_decisions", ["skill_version_id"]
    )
    op.create_table(
        "skill_events",
        sa.Column("id", uuid, nullable=False),
        sa.Column("skill_id", uuid, nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=128), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", timestamp, nullable=False),
        sa.CheckConstraint("sequence >= 1", name="sequence_positive"),
        sa.ForeignKeyConstraint(["skill_id"], ["skills.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("skill_id", "sequence"),
    )
    op.create_index("ix_skill_events_skill_id", "skill_events", ["skill_id"])


def downgrade() -> None:
    op.drop_index("ix_skill_events_skill_id", table_name="skill_events")
    op.drop_table("skill_events")
    op.drop_index("ix_promotion_decisions_skill_version_id", table_name="promotion_decisions")
    op.drop_table("promotion_decisions")
    op.drop_index("ix_run_skill_selections_run_id", table_name="run_skill_selections")
    op.drop_table("run_skill_selections")
    op.drop_index("ix_skill_sources_skill_version_id", table_name="skill_sources")
    op.drop_table("skill_sources")
    op.drop_index("ix_eval_runs_experiment_id", table_name="eval_runs")
    op.drop_index("ix_eval_runs_eval_case_id", table_name="eval_runs")
    op.drop_table("eval_runs")
    op.drop_index("ix_eval_experiments_dataset_id", table_name="eval_experiments")
    op.drop_table("eval_experiments")
    op.drop_index("ix_eval_cases_task_family", table_name="eval_cases")
    op.drop_index("ix_eval_cases_dataset_id", table_name="eval_cases")
    op.drop_table("eval_cases")
    op.drop_table("eval_datasets")
    with op.batch_alter_table("runs") as batch:
        batch.drop_constraint("fk_runs_pinned_skill_version_id_skill_versions", type_="foreignkey")
    with op.batch_alter_table("skills") as batch:
        batch.drop_constraint("fk_skills_active_version_id_skill_versions", type_="foreignkey")
    op.drop_index("ix_skill_versions_skill_id", table_name="skill_versions")
    op.drop_table("skill_versions")
    op.drop_table("skills")
    with op.batch_alter_table("runs") as batch:
        batch.drop_constraint("ck_runs_run_mode_valid", type_="check")
        batch.drop_constraint("ck_runs_pinned_skill_present", type_="check")
        batch.drop_column("config_hash")
        batch.drop_column("config_snapshot")
        batch.drop_column("pinned_skill_version_id")
        batch.drop_column("run_mode")
