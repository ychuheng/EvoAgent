"""Widen the run status column for the new terminal state.

`authorization_revoked` 比原有最长状态值长，StrEnum 列以 VARCHAR 存储时长度不同，
`alembic check` 会把它报成类型变化；这里按方言分别处理，保证迁移与元数据一致。

Revision ID: 20260927_0016
Revises: 20260927_0015
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260927_0016"
down_revision: str | Sequence[str] | None = "20260927_0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RUN_STATUS = sa.Enum(
    "queued",
    "running",
    "waiting_user",
    "retrying",
    "paused",
    "recovering",
    "completed",
    "failed",
    "cancelled",
    "timeout",
    "limit_reached",
    "authorization_revoked",
    name="run_status",
    native_enum=False,
    validate_strings=True,
)
LEGACY_RUN_STATUS = sa.Enum(
    "queued",
    "running",
    "waiting_user",
    "retrying",
    "paused",
    "recovering",
    "completed",
    "failed",
    "cancelled",
    "timeout",
    "limit_reached",
    name="run_status",
    native_enum=False,
    validate_strings=True,
)


def upgrade() -> None:
    with op.batch_alter_table("runs") as batch:
        batch.alter_column(
            "status",
            existing_type=LEGACY_RUN_STATUS,
            type_=RUN_STATUS,
            existing_nullable=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("runs") as batch:
        batch.alter_column(
            "status",
            existing_type=RUN_STATUS,
            type_=LEGACY_RUN_STATUS,
            existing_nullable=False,
        )
