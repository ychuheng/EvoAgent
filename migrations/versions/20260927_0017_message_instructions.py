"""Track when a mid-run instruction was injected into the model context.

Revision ID: 20260927_0017
Revises: 20260927_0016
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260927_0017"
down_revision: str | Sequence[str] | None = "20260927_0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("messages", sa.Column("injected_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("messages", "injected_at")
