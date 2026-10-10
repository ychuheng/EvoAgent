"""Freeze validation replica provenance separately from mutable outputs."""

import sqlalchemy as sa
from alembic import op

revision = "20261010_0029"
down_revision = "20261010_0028"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "validation_replica_bindings",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.Uuid(),
            sa.ForeignKey("workspaces.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "request_id",
            sa.Uuid(),
            sa.ForeignKey("learning_requests.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "eval_run_id",
            sa.Uuid(),
            sa.ForeignKey("eval_runs.id", ondelete="RESTRICT"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "run_id",
            sa.Uuid(),
            sa.ForeignKey("runs.id", ondelete="RESTRICT"),
            nullable=False,
            unique=True,
        ),
        sa.Column("case_key", sa.String(64), nullable=False),
        sa.Column("arm", sa.String(16), nullable=False),
        sa.Column("repeat_index", sa.Integer(), nullable=False),
        sa.Column("fixture_id", sa.String(64), nullable=False),
        sa.Column("input_fingerprint", sa.String(71), nullable=False),
        sa.Column("manifest", sa.JSON(), nullable=False),
        sa.Column("manifest_hash", sa.String(71), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("request_id", "case_key", "arm", "repeat_index"),
        sa.CheckConstraint("arm IN ('control','treatment')", name="replica_arm_valid"),
        sa.CheckConstraint("repeat_index >= 0 AND repeat_index < 3", name="replica_repeat_valid"),
    )


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT count(*) FROM validation_replica_bindings")):
        raise RuntimeError("validation replica evidence prevents destructive downgrade")
    op.drop_table("validation_replica_bindings")
