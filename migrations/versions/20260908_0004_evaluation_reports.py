"""为阶段三评测报告和门禁增加不可变引用。

Revision ID: 20260908_0004
Revises: 20260908_0003
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260908_0004"
down_revision: str | None = "20260908_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("skill_versions") as batch:
        batch.add_column(sa.Column("evaluation_report_hash", sa.String(length=71), nullable=True))
        batch.add_column(sa.Column("gate_report_hash", sa.String(length=71), nullable=True))

    with op.batch_alter_table("eval_experiments") as batch:
        batch.add_column(sa.Column("report_artifact_id", sa.Uuid(), nullable=True))
        batch.add_column(sa.Column("report_hash", sa.String(length=71), nullable=True))
        batch.add_column(sa.Column("gate_report_hash", sa.String(length=71), nullable=True))
        batch.create_foreign_key(
            "fk_eval_experiments_report_artifact_id_artifacts",
            "artifacts",
            ["report_artifact_id"],
            ["id"],
            ondelete="RESTRICT",
        )


def downgrade() -> None:
    with op.batch_alter_table("eval_experiments") as batch:
        batch.drop_constraint(
            "fk_eval_experiments_report_artifact_id_artifacts", type_="foreignkey"
        )
        batch.drop_column("gate_report_hash")
        batch.drop_column("report_hash")
        batch.drop_column("report_artifact_id")

    with op.batch_alter_table("skill_versions") as batch:
        batch.drop_column("gate_report_hash")
        batch.drop_column("evaluation_report_hash")
