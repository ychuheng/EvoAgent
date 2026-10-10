"""Keep legacy selections unchanged and freeze complete v3 injection evidence."""

import sqlalchemy as sa
from alembic import op

revision = "20261010_0032"
down_revision = "20261010_0031"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("run_skill_selections") as batch:
        batch.add_column(sa.Column("content_hash", sa.String(71), nullable=True))
        batch.add_column(sa.Column("applicability", sa.JSON(none_as_null=True), nullable=True))
        batch.add_column(sa.Column("selection_policy_version", sa.String(32), nullable=True))
        batch.create_check_constraint(
            "v3_evidence_complete",
            "(content_hash IS NULL AND applicability IS NULL AND selection_policy_version IS NULL) "
            "OR (content_hash IS NOT NULL AND applicability IS NOT NULL "
            "AND selection_policy_version IS NOT NULL "
            "AND selection_policy_version = 'skill-selector-v1' "
            "AND rendered_hash IS NOT NULL AND scope_key IS NOT NULL "
            "AND length(content_hash) = 71 AND content_hash LIKE 'sha256:%' "
            "AND length(rendered_hash) = 71 AND rendered_hash LIKE 'sha256:%' "
            "AND ((origin = 'trial' AND trial_id IS NOT NULL) "
            "OR (origin IN ('formal','pinned') AND trial_id IS NULL)))",
        )
    op.create_index(
        "uq_run_skill_selections_v3_run",
        "run_skill_selections",
        ["run_id"],
        unique=True,
        postgresql_where=sa.text("selection_policy_version IS NOT NULL"),
        sqlite_where=sa.text("selection_policy_version IS NOT NULL"),
    )


def downgrade():
    if op.get_bind().scalar(
        sa.text(
            "SELECT count(*) FROM run_skill_selections WHERE content_hash IS NOT NULL "
            "OR applicability IS NOT NULL OR selection_policy_version IS NOT NULL"
        )
    ):
        raise RuntimeError("cannot downgrade while frozen v3 selection evidence exists")
    op.drop_index("uq_run_skill_selections_v3_run", table_name="run_skill_selections")
    with op.batch_alter_table("run_skill_selections") as batch:
        batch.drop_constraint("v3_evidence_complete", type_="check")
        batch.drop_column("selection_policy_version")
        batch.drop_column("applicability")
        batch.drop_column("content_hash")
