"""Freeze a Task's input file set with content hashes.

Revision ID: 20260927_0018
Revises: 20260927_0017
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260927_0018"
down_revision: str | Sequence[str] | None = "20260927_0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column("frozen_inputs", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("tasks", "frozen_inputs")
