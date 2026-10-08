"""Add injection-gate check metadata to artifacts (M-A0).

Revision ID: 20261008_0020
Revises: 20260927_0019

只加列与一条一致性约束，**不回填**：旧行保持 NULL/unchecked，表示"未按当前策略
检查过"。任何把旧行批量标成 verified 的写法都是伪造检查结论，禁止。

`redaction_status` 是 NOT NULL：加列时用 server_default 回填成 `unchecked`（模型侧
用 Python 默认值，不需要 server_default）。`redaction_policy_version` 保持可空——
NULL 表示策略版本未知，门禁据此重新复查，而不是假设它已通过。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261008_0020"
down_revision: str | Sequence[str] | None = "20260927_0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CONSISTENCY = (
    "redaction_status <> 'verified' OR "
    "(redaction_policy_version IS NOT NULL AND redaction_checked_hash IS NOT NULL)"
)
CONSTRAINT_NAME = "ck_artifacts_redaction_verified_requires_metadata"


def upgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.add_column(
            "artifacts", sa.Column("redaction_policy_version", sa.Integer(), nullable=True)
        )
        op.add_column(
            "artifacts", sa.Column("redaction_checked_hash", sa.String(length=128), nullable=True)
        )
        op.add_column(
            "artifacts",
            sa.Column(
                "redaction_status",
                sa.String(length=16),
                nullable=False,
                server_default="unchecked",
            ),
        )
        op.create_check_constraint(op.f(CONSTRAINT_NAME), "artifacts", CONSISTENCY)
    else:
        with op.batch_alter_table("artifacts") as batch:
            batch.add_column(sa.Column("redaction_policy_version", sa.Integer(), nullable=True))
            batch.add_column(
                sa.Column("redaction_checked_hash", sa.String(length=128), nullable=True)
            )
            batch.add_column(
                sa.Column(
                    "redaction_status",
                    sa.String(length=16),
                    nullable=False,
                    server_default="unchecked",
                )
            )
            batch.create_check_constraint(op.f(CONSTRAINT_NAME), CONSISTENCY)


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.drop_constraint(op.f(CONSTRAINT_NAME), "artifacts", type_="check")
        op.drop_column("artifacts", "redaction_status")
        op.drop_column("artifacts", "redaction_checked_hash")
        op.drop_column("artifacts", "redaction_policy_version")
    else:
        with op.batch_alter_table("artifacts") as batch:
            batch.drop_constraint(op.f(CONSTRAINT_NAME), type_="check")
            batch.drop_column("redaction_status")
            batch.drop_column("redaction_checked_hash")
            batch.drop_column("redaction_policy_version")
