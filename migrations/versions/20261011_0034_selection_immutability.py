"""Protect v3 selection evidence below the ORM layer."""

import sqlalchemy as sa
from alembic import op

from evoagent.db.selection_immutability import FUNCTION_SQL, SQLITE_TRIGGER_SQL, TRIGGER_SQL

revision = "20261011_0034"
down_revision = "20261010_0033"
branch_labels = None
depends_on = None


def upgrade():
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute(FUNCTION_SQL)
        op.execute(TRIGGER_SQL)
    elif dialect == "sqlite":
        op.execute(SQLITE_TRIGGER_SQL)
    else:
        raise RuntimeError("selection evidence protection requires PostgreSQL or SQLite")


def downgrade():
    if op.get_bind().scalar(
        sa.text("SELECT count(*) FROM run_skill_selections WHERE content_hash IS NOT NULL")
    ):
        raise RuntimeError(
            "cannot remove database protection while frozen v3 selection evidence exists"
        )
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP TRIGGER v3_skill_selection_immutable ON run_skill_selections")
        op.execute("DROP FUNCTION protect_v3_skill_selection()")
    else:
        op.execute("DROP TRIGGER v3_skill_selection_immutable")
