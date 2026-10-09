"""Preserve independent human validation judgments and report revisions."""

import sqlalchemy as sa
from alembic import op

revision = "20261010_0028"
down_revision = "20261009_0027"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "validation_judgments",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("workspace_id", sa.Uuid(), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("request_id", sa.Uuid(), sa.ForeignKey("learning_requests.id"), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("client_request_id", sa.String(128), nullable=False),
        sa.Column("request_body_hash", sa.String(71), nullable=False),
        sa.Column("actor", sa.String(128), nullable=False),
        sa.Column("base_report_hash", sa.String(71), nullable=False),
        sa.Column("judgments", sa.JSON(), nullable=False),
        sa.Column("resulting_report", sa.JSON(), nullable=False),
        sa.Column("resulting_report_hash", sa.String(71), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "workspace_id", "client_request_id", name="uq_validation_judgment_client"
        ),
        sa.UniqueConstraint("request_id", "revision", name="uq_validation_judgment_revision"),
        sa.CheckConstraint("revision > 0", name="revision_positive"),
    )


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT count(*) FROM validation_judgments")):
        raise RuntimeError("human validation evidence prevents destructive downgrade")
    op.drop_table("validation_judgments")
