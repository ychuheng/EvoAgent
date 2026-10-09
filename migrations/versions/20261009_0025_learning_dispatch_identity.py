"""Durable sender identity and frozen price assumptions for learning calls."""

import sqlalchemy as sa
from alembic import op

revision = "20261009_0025"
down_revision = "20261009_0024"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("learning_spend_reservations") as batch:
        batch.add_column(sa.Column("job_id", sa.Uuid(), nullable=True))
        batch.add_column(sa.Column("job_epoch", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("scope", sa.String(32), nullable=False, server_default="legacy"))
        batch.add_column(sa.Column("input_price_micros_per_million", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("output_price_micros_per_million", sa.Integer(), nullable=True))
        batch.create_foreign_key("fk_learning_spend_job", "maintenance_jobs", ["job_id"], ["id"])


def downgrade():
    with op.batch_alter_table("learning_spend_reservations") as batch:
        batch.drop_constraint("fk_learning_spend_job", type_="foreignkey")
        for name in (
            "output_price_micros_per_million",
            "input_price_micros_per_million",
            "scope",
            "job_epoch",
            "job_id",
        ):
            batch.drop_column(name)
