"""Freeze an explicit user task category without classifying historical goals."""

import sqlalchemy as sa
from alembic import op

revision = "20261010_0031"
down_revision = "20261010_0030"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("tasks") as batch:
        batch.add_column(sa.Column("family", sa.String(32), nullable=True))
        batch.create_check_constraint(
            "family_valid",
            "family IS NULL OR family IN "
            "('general','coding','research','document','data','file_management')",
        )


def downgrade():
    connection = op.get_bind()
    if connection.scalar(sa.text("SELECT count(*) FROM tasks WHERE family IS NOT NULL")):
        raise RuntimeError("cannot downgrade while frozen task family evidence exists")
    with op.batch_alter_table("tasks") as batch:
        batch.drop_constraint("family_valid", type_="check")
        batch.drop_column("family")
