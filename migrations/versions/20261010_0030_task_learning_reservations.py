"""Bind learning dispatch to either a maintenance job or a real task lease."""

import sqlalchemy as sa
from alembic import op

revision = "20261010_0030"
down_revision = "20261010_0029"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("learning_spend_reservations") as batch:
        batch.add_column(
            sa.Column(
                "dispatcher_kind", sa.String(16), nullable=False, server_default="maintenance"
            )
        )
        batch.add_column(sa.Column("task_id", sa.Uuid(), nullable=True))
        batch.add_column(sa.Column("run_id", sa.Uuid(), nullable=True))
        batch.add_column(sa.Column("task_epoch", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("request_body_hash", sa.String(71), nullable=True))
        batch.create_foreign_key("fk_learning_spend_task", "tasks", ["task_id"], ["id"])
        batch.create_foreign_key("fk_learning_spend_run", "runs", ["run_id"], ["id"])
        batch.create_check_constraint(
            "learning_dispatch_identity",
            "(dispatcher_kind = 'maintenance' AND task_id IS NULL AND run_id IS NULL "
            "AND task_epoch IS NULL) OR (dispatcher_kind = 'task' AND task_id IS NOT NULL "
            "AND run_id IS NOT NULL AND task_epoch IS NOT NULL AND job_id IS NULL "
            "AND job_epoch IS NULL AND request_body_hash IS NOT NULL)",
        )


def downgrade():
    if op.get_bind().scalar(
        sa.text("SELECT count(*) FROM learning_spend_reservations WHERE dispatcher_kind = 'task'")
    ):
        raise RuntimeError("task learning spend evidence prevents destructive downgrade")
    with op.batch_alter_table("learning_spend_reservations") as batch:
        batch.drop_constraint("learning_dispatch_identity", type_="check")
        batch.drop_constraint("fk_learning_spend_task", type_="foreignkey")
        batch.drop_constraint("fk_learning_spend_run", type_="foreignkey")
        for name in ("request_body_hash", "task_epoch", "run_id", "task_id", "dispatcher_kind"):
            batch.drop_column(name)
