"""Leave queued historical tasks on their frozen legacy selection contract."""

import sqlalchemy as sa
from alembic import op

revision = "20261010_0033"
down_revision = "20261010_0032"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("tasks") as batch:
        batch.add_column(sa.Column("selection_contract_version", sa.Integer(), nullable=True))
        batch.create_check_constraint(
            "selection_contract_valid",
            "selection_contract_version IS NULL OR selection_contract_version = 3",
        )


def downgrade():
    if op.get_bind().scalar(
        sa.text("SELECT count(*) FROM tasks WHERE selection_contract_version IS NOT NULL")
    ):
        raise RuntimeError("cannot downgrade while tasks require v3 selection routing")
    with op.batch_alter_table("tasks") as batch:
        batch.drop_constraint("selection_contract_valid", type_="check")
        batch.drop_column("selection_contract_version")
