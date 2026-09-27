"""Append-only spend ledger for the M0c budget gate.

Revision ID: 20260927_0019
Revises: 20260927_0018
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260927_0019"
down_revision: str | Sequence[str] | None = "20260927_0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "spend_records",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("scope", sa.String(length=32), nullable=False),
        sa.Column(
            "task_id",
            sa.Uuid(),
            sa.ForeignKey("tasks.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("run_id", sa.Uuid(), nullable=True),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("model", sa.String(length=256), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("cost_micros", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("input_price_micros_per_million", sa.Integer(), nullable=True),
        sa.Column("output_price_micros_per_million", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("input_tokens >= 0", name="input_tokens_non_negative"),
        sa.CheckConstraint("output_tokens >= 0", name="output_tokens_non_negative"),
        sa.CheckConstraint("cost_micros >= 0", name="cost_micros_non_negative"),
    )
    op.create_index("ix_spend_records_task_id", "spend_records", ["task_id"])
    op.create_index("ix_spend_scope_created", "spend_records", ["scope", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_spend_scope_created", table_name="spend_records")
    op.drop_index("ix_spend_records_task_id", table_name="spend_records")
    op.drop_table("spend_records")
