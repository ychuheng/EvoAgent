"""Separate runtime experiments from Skill comparison experiments."""

import sqlalchemy as sa
from alembic import op

revision = "20260922_0012"
down_revision = "20260922_0011"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "runtime_experiments",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("dataset_id", sa.Uuid(), sa.ForeignKey("eval_datasets.id"), nullable=False),
        sa.Column("spec", sa.JSON(), nullable=False),
        sa.Column("spec_hash", sa.String(71), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("report", sa.JSON()),
        sa.Column("report_hash", sa.String(71)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "runtime_eval_runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "experiment_id", sa.Uuid(), sa.ForeignKey("runtime_experiments.id"), nullable=False
        ),
        sa.Column("case_id", sa.Uuid(), sa.ForeignKey("eval_cases.id"), nullable=False),
        sa.Column("arm", sa.String(16), nullable=False),
        sa.Column("repeat_index", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.Uuid(), sa.ForeignKey("runs.id"), nullable=False, unique=True),
        sa.Column("metrics", sa.JSON()),
        sa.Column("validation_results", sa.JSON()),
        sa.UniqueConstraint("experiment_id", "case_id", "arm", "repeat_index"),
    )
    op.create_index("ix_runtime_eval_runs_experiment_id", "runtime_eval_runs", ["experiment_id"])


def downgrade():
    op.drop_table("runtime_eval_runs")
    op.drop_table("runtime_experiments")
