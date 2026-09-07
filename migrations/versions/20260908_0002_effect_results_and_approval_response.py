"""保存副作用结果与审批答复。

Revision ID: 20260908_0002
Revises: 20260906_0001
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260908_0002"
down_revision: str | None = "20260906_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("tool_effects", sa.Column("result_content", sa.Text(), nullable=True))
    op.add_column("tool_approvals", sa.Column("response", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("tool_approvals", "response")
    op.drop_column("tool_effects", "result_content")
