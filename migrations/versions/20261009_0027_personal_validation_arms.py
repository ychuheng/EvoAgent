"""Distinguish personal development validation from formal holdout experiments."""

import sqlalchemy as sa
from alembic import op

revision = "20261009_0027"
down_revision = "20261009_0026"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("eval_datasets") as batch:
        batch.add_column(
            sa.Column("purpose", sa.String(32), nullable=False, server_default="formal")
        )
        batch.create_check_constraint("purpose_valid", "purpose IN ('formal','personal_dev')")
    with op.batch_alter_table("eval_experiments") as batch:
        batch.add_column(
            sa.Column("purpose", sa.String(32), nullable=False, server_default="formal")
        )
        batch.create_check_constraint(
            "purpose_valid", "purpose IN ('formal','personal_validation')"
        )
        batch.add_column(sa.Column("comparison_version_id", sa.Uuid()))
        batch.add_column(sa.Column("learning_request_id", sa.Uuid()))
        batch.create_foreign_key(
            "fk_eval_experiments_comparison_version_id_skill_versions",
            "skill_versions",
            ["comparison_version_id"],
            ["id"],
        )
        batch.create_foreign_key(
            "fk_eval_experiments_learning_request",
            "learning_requests",
            ["learning_request_id"],
            ["id"],
        )
        batch.create_unique_constraint(
            "uq_eval_experiments_learning_request_id", ["learning_request_id"]
        )
    with op.batch_alter_table("eval_runs") as batch:
        batch.add_column(sa.Column("arm", sa.String(16), nullable=True))
    op.execute(
        "UPDATE eval_runs SET arm = CASE WHEN mode = 'baseline' THEN 'control' ELSE 'treatment' END"
    )
    with op.batch_alter_table("eval_runs") as batch:
        batch.alter_column("arm", existing_type=sa.String(16), nullable=False)
        batch.drop_constraint("uq_eval_runs_experiment_id", type_="unique")
        batch.create_unique_constraint(
            "uq_eval_runs_experiment_id", ["experiment_id", "eval_case_id", "arm", "repeat_index"]
        )
        batch.create_check_constraint("arm_valid", "arm IN ('control','treatment')")


def downgrade():
    # A two-pinned-arm experiment cannot fit the old mode identity. Refuse a
    # destructive downgrade rather than discarding one arm or relabeling it.
    collision = op.get_bind().scalar(
        sa.text(
            "SELECT count(*) FROM (SELECT experiment_id,eval_case_id,mode,repeat_index "
            "FROM eval_runs GROUP BY experiment_id,eval_case_id,mode,repeat_index "
            "HAVING count(*) > 1) AS collisions"
        )
    )
    if collision:
        raise RuntimeError("personal two-pinned-arm data prevents legacy downgrade")
    with op.batch_alter_table("eval_runs") as batch:
        batch.drop_constraint("uq_eval_runs_experiment_id", type_="unique")
        batch.create_unique_constraint(
            "uq_eval_runs_experiment_id", ["experiment_id", "eval_case_id", "mode", "repeat_index"]
        )
        batch.drop_constraint(op.f("ck_eval_runs_arm_valid"), type_="check")
        batch.drop_column("arm")
    with op.batch_alter_table("eval_experiments") as batch:
        batch.drop_constraint(op.f("ck_eval_experiments_purpose_valid"), type_="check")
        batch.drop_constraint("uq_eval_experiments_learning_request_id", type_="unique")
        batch.drop_constraint("fk_eval_experiments_learning_request", type_="foreignkey")
        batch.drop_constraint(
            "fk_eval_experiments_comparison_version_id_skill_versions", type_="foreignkey"
        )
        for name in ("learning_request_id", "comparison_version_id", "purpose"):
            batch.drop_column(name)
    with op.batch_alter_table("eval_datasets") as batch:
        batch.drop_constraint(op.f("ck_eval_datasets_purpose_valid"), type_="check")
        batch.drop_column("purpose")
