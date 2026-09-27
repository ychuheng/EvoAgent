"""Create authorized project roots and freeze the Task binding.

Revision ID: 20260927_0015
Revises: 20260926_0014
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260927_0015"
down_revision: str | Sequence[str] | None = "20260926_0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

AUTHORIZATION = sa.Enum(
    "read",
    "read_write",
    name="project_authorization",
    native_enum=False,
    validate_strings=True,
)
STATUS = sa.Enum(
    "available",
    "unavailable",
    "revoked",
    name="project_status",
    native_enum=False,
    validate_strings=True,
)
EVENT_AUTHORIZATION = sa.Enum(
    "read",
    "read_write",
    name="project_event_authorization",
    native_enum=False,
    validate_strings=True,
)


def upgrade() -> None:
    op.create_table(
        "projects",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.String(length=256), nullable=False),
        sa.Column("root", sa.String(length=2048), nullable=False),
        sa.Column("authorization", AUTHORIZATION, nullable=False),
        sa.Column("status", STATUS, nullable=False),
        sa.Column("authorization_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "workspace_id",
            sa.Uuid(),
            sa.ForeignKey("workspaces.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("lock_version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("authorization_version >= 1", name="authorization_version_positive"),
        sa.CheckConstraint("length(root) <= 2048", name="root_length"),
    )
    op.create_index("ix_projects_root", "projects", ["root"], unique=True)

    op.create_table(
        "project_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "project_id",
            sa.Uuid(),
            sa.ForeignKey("projects.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("authorization", EVENT_AUTHORIZATION, nullable=False),
        sa.Column("authorization_version", sa.Integer(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("project_id", "sequence"),
        sa.CheckConstraint("sequence >= 1", name="sequence_positive"),
    )
    op.create_index("ix_project_events_project_id", "project_events", ["project_id"])

    op.add_column("sessions", sa.Column("project_id", sa.Uuid(), nullable=True))
    # SQLite 不支持 ALTER 约束，必须走 batch 模式（与既有迁移保持一致）。
    with op.batch_alter_table("sessions") as batch:
        batch.create_foreign_key(
            "fk_sessions_project_id_projects",
            "projects",
            ["project_id"],
            ["id"],
            ondelete="RESTRICT",
        )
    op.create_index("ix_sessions_project_id", "sessions", ["project_id"])

    op.add_column("tasks", sa.Column("project_id", sa.Uuid(), nullable=True))
    op.add_column("tasks", sa.Column("project_authorization_version", sa.Integer(), nullable=True))
    with op.batch_alter_table("tasks") as batch:
        batch.create_foreign_key(
            "fk_tasks_project_id_projects",
            "projects",
            ["project_id"],
            ["id"],
            ondelete="RESTRICT",
        )
    op.create_index("ix_tasks_project_id", "tasks", ["project_id"])


def downgrade() -> None:
    op.drop_index("ix_tasks_project_id", table_name="tasks")
    with op.batch_alter_table("tasks") as batch:
        batch.drop_constraint("fk_tasks_project_id_projects", type_="foreignkey")
    op.drop_column("tasks", "project_authorization_version")
    op.drop_column("tasks", "project_id")

    op.drop_index("ix_sessions_project_id", table_name="sessions")
    with op.batch_alter_table("sessions") as batch:
        batch.drop_constraint("fk_sessions_project_id_projects", type_="foreignkey")
    op.drop_column("sessions", "project_id")

    op.drop_index("ix_project_events_project_id", table_name="project_events")
    op.drop_table("project_events")

    op.drop_index("ix_projects_root", table_name="projects")
    op.drop_table("projects")
