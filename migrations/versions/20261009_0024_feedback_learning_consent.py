"""Persist explicit method-learning consent; historical feedback is not consent."""

import sqlalchemy as sa
from alembic import op

revision = "20261009_0024"
down_revision = "20261009_0023"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "run_feedback",
        sa.Column("learn_from_feedback", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade():
    op.drop_column("run_feedback", "learn_from_feedback")
