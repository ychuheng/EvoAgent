"""sandbox executions
Revision ID: 20260921_0010
Revises: 20260921_0009
"""

import sqlalchemy as sa
from alembic import op

revision = "20260921_0010"
down_revision = "20260921_0009"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "sandbox_executions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("run_id", sa.Uuid(), sa.ForeignKey("runs.id", ondelete="RESTRICT")),
        sa.Column("server_id", sa.Uuid(), sa.ForeignKey("mcp_servers.id", ondelete="RESTRICT")),
        sa.Column("lease_epoch", sa.Integer()),
        sa.Column("profile_hash", sa.String(71), nullable=False),
        sa.Column("container_id", sa.String(128)),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("error_code", sa.String(128)),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
    )


def downgrade():
    op.drop_table("sandbox_executions")
