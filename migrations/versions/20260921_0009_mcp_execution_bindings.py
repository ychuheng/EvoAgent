"""MCP execution bindings and frozen Run catalogs.

Revision ID: 20260921_0009
Revises: 20260920_0008
"""

import sqlalchemy as sa
from alembic import op

revision = "20260921_0009"
down_revision = "20260920_0008"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("runs", sa.Column("tool_catalog_snapshot", sa.JSON(), nullable=True))
    op.add_column("tool_calls", sa.Column("execution_binding", sa.JSON(), nullable=True))
    op.add_column(
        "mcp_servers",
        sa.Column("execution_state", sa.String(16), server_default="disabled", nullable=False),
    )
    op.add_column("mcp_servers", sa.Column("active_catalog_id", sa.Uuid(), nullable=True))
    op.add_column(
        "mcp_servers",
        sa.Column("execution_version", sa.Integer(), server_default="0", nullable=False),
    )


def downgrade():
    op.drop_column("mcp_servers", "execution_version")
    op.drop_column("mcp_servers", "active_catalog_id")
    op.drop_column("mcp_servers", "execution_state")
    op.drop_column("tool_calls", "execution_binding")
    op.drop_column("runs", "tool_catalog_snapshot")
