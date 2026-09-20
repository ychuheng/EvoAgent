"""阶段四租约代次和 Skill 零命中冻结标记。"""

import sqlalchemy as sa
from alembic import op

revision = "20260919_0005"
down_revision = "20260908_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("tasks") as batch:
        batch.add_column(sa.Column("lease_epoch", sa.Integer(), nullable=False, server_default="0"))
    with op.batch_alter_table("runs") as batch:
        batch.add_column(
            sa.Column(
                "skill_selection_frozen", sa.Boolean(), nullable=False, server_default=sa.false()
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("runs") as batch:
        batch.drop_column("skill_selection_frozen")
    with op.batch_alter_table("tasks") as batch:
        batch.drop_column("lease_epoch")
