"""Fence Skill evaluation coordinators with a monotonically increasing epoch."""

import sqlalchemy as sa
from alembic import op

revision = "20260922_0011"
down_revision = "20260921_0010"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "eval_experiments",
        sa.Column("lease_epoch", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade():
    op.drop_column("eval_experiments", "lease_epoch")
