"""Commit-scoped PostgreSQL cancellation hints; no historical row changes."""

from alembic import op

revision = "20261009_0023"
down_revision = "20261009_0022"
branch_labels = None
depends_on = None


def upgrade():
    if op.get_bind().dialect.name == "postgresql":
        op.execute("""
            CREATE OR REPLACE FUNCTION evoagent_notify_task_cancel() RETURNS trigger
            LANGUAGE plpgsql AS $$
            BEGIN
                PERFORM pg_catalog.pg_notify('evoagent_task_cancel', NEW.id::text);
                RETURN NEW;
            END $$
        """)
        op.execute("""
            CREATE TRIGGER tasks_cancel_committed AFTER UPDATE OF cancel_requested ON tasks
            FOR EACH ROW WHEN (NOT OLD.cancel_requested AND NEW.cancel_requested)
            EXECUTE FUNCTION evoagent_notify_task_cancel()
        """)


def downgrade():
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP TRIGGER IF EXISTS tasks_cancel_committed ON tasks")
        op.execute("DROP FUNCTION IF EXISTS evoagent_notify_task_cancel()")
