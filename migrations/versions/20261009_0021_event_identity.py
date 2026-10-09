"""Persist event identities without rewriting historical evidence."""

import sqlalchemy as sa
from alembic import op

revision = "20261009_0021"
down_revision = "20261008_0020"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("run_events", sa.Column("dedupe_key", sa.String(71), nullable=True))
    with op.batch_alter_table("run_events") as batch:
        batch.create_unique_constraint("uq_run_events_dedupe_key", ["run_id", "dedupe_key"])


def downgrade():
    with op.batch_alter_table("run_events") as batch:
        batch.drop_constraint("uq_run_events_dedupe_key", type_="unique")
        batch.drop_column("dedupe_key")
