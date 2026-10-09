"""Independent personal trial bindings and immutable usage observations."""

import sqlalchemy as sa
from alembic import op

revision = "20261009_0026"
down_revision = "20261009_0025"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "skill_trials",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "skill_id", sa.Uuid(), sa.ForeignKey("skills.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column(
            "version_id",
            sa.Uuid(),
            sa.ForeignKey("skill_versions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("workspace_id", sa.Uuid(), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("project_id", sa.Uuid(), sa.ForeignKey("projects.id")),
        sa.Column("scope_key", sa.String(128), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column(
            "validation_request_id",
            sa.Uuid(),
            sa.ForeignKey("learning_requests.id"),
            nullable=False,
        ),
        sa.Column("report_hash", sa.String(71), nullable=False),
        sa.Column("health_policy_snapshot", sa.JSON(), nullable=False),
        sa.Column("health_policy_hash", sa.String(71), nullable=False),
        sa.Column("suspended_at", sa.DateTime(timezone=True)),
        sa.Column("suspension_reason", sa.String(1000)),
        sa.Column("reviewer", sa.String(128), nullable=False),
        sa.Column("reason", sa.String(1000), nullable=False),
        sa.Column("lock_version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status IN ('active','suspended','replaced')", name="status_valid"),
        sa.CheckConstraint("lock_version >= 0", name="lock_version_non_negative"),
    )
    op.create_index("ix_skill_trials_skill_id", "skill_trials", ["skill_id"])
    op.create_index(
        "uq_skill_trials_active_scope",
        "skill_trials",
        ["skill_id", "scope_key"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
        sqlite_where=sa.text("status = 'active'"),
    )
    with op.batch_alter_table("run_skill_selections") as batch:
        batch.add_column(sa.Column("trial_id", sa.Uuid()))
        batch.add_column(
            sa.Column("origin", sa.String(16), nullable=False, server_default="legacy")
        )
        batch.add_column(sa.Column("scope_key", sa.String(128)))
        batch.add_column(sa.Column("rendered_hash", sa.String(71)))
        batch.create_foreign_key(
            "fk_run_skill_selections_trial_id_skill_trials", "skill_trials", ["trial_id"], ["id"]
        )
    op.create_table(
        "skill_observations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("run_id", sa.Uuid(), sa.ForeignKey("runs.id"), nullable=False),
        sa.Column("version_id", sa.Uuid(), sa.ForeignKey("skill_versions.id"), nullable=False),
        sa.Column("trial_id", sa.Uuid(), sa.ForeignKey("skill_trials.id")),
        sa.Column(
            "selection_id", sa.Uuid(), sa.ForeignKey("run_skill_selections.id"), nullable=False
        ),
        sa.Column("feedback_revision", sa.Integer(), nullable=False),
        sa.Column("outcome", sa.String(32), nullable=False),
        sa.Column("attribution", sa.String(32), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("first_finished_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("input_fingerprint", sa.String(71), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("run_id", "version_id", "feedback_revision"),
        sa.CheckConstraint("feedback_revision >= 0", name="feedback_revision_non_negative"),
        sa.CheckConstraint(
            "outcome IN ('verified_success','verified_failure','unknown')", name="outcome_valid"
        ),
        sa.CheckConstraint(
            "attribution IN ('skill_related','environment','user_request','uncertain')",
            name="attribution_valid",
        ),
    )
    op.create_index(
        "ix_skill_observations_trial_finished",
        "skill_observations",
        ["trial_id", "first_finished_at"],
    )


def downgrade():
    op.drop_table("skill_observations")
    with op.batch_alter_table("run_skill_selections") as batch:
        batch.drop_constraint("fk_run_skill_selections_trial_id_skill_trials", type_="foreignkey")
        for column in ("rendered_hash", "scope_key", "origin", "trial_id"):
            batch.drop_column(column)
    op.drop_table("skill_trials")
