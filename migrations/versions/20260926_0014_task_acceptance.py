"""Store explicit task acceptance conditions.

Revision ID: 20260926_0014
Revises: 20260925_0013
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260926_0014"
down_revision: str | Sequence[str] | None = "20260925_0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column("acceptance", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("tasks", "acceptance")
