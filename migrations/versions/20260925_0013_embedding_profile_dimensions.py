"""Allow native embedding dimensions per immutable model profile.

Revision ID: 20260925_0013
Revises: 20260922_0012
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260925_0013"
down_revision: str | Sequence[str] | None = "20260922_0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.drop_constraint(
            op.f("ck_embedding_profiles_fixed_dimension"), "embedding_profiles", type_="check"
        )
        op.create_check_constraint(
            op.f("ck_embedding_profiles_supported_dimension"),
            "embedding_profiles",
            "dimension BETWEEN 1 AND 2000",
        )
        op.execute(
            "ALTER TABLE document_embeddings ALTER COLUMN vector TYPE vector USING vector::vector"
        )
    else:
        with op.batch_alter_table("embedding_profiles") as batch:
            batch.drop_constraint(op.f("ck_embedding_profiles_fixed_dimension"), type_="check")
            batch.create_check_constraint(
                op.f("ck_embedding_profiles_supported_dimension"), "dimension BETWEEN 1 AND 2000"
            )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        count = (
            op.get_bind()
            .exec_driver_sql("SELECT count(*) FROM embedding_profiles WHERE dimension <> 1536")
            .scalar()
        )
        if count:
            raise RuntimeError("remove non-1536 embedding profiles before downgrade")
        op.execute(
            "ALTER TABLE document_embeddings ALTER COLUMN vector TYPE vector(1536) "
            "USING vector::vector(1536)"
        )
        op.drop_constraint(
            op.f("ck_embedding_profiles_supported_dimension"), "embedding_profiles", type_="check"
        )
        op.create_check_constraint(
            op.f("ck_embedding_profiles_fixed_dimension"), "embedding_profiles", "dimension = 1536"
        )
    else:
        with op.batch_alter_table("embedding_profiles") as batch:
            batch.drop_constraint(op.f("ck_embedding_profiles_supported_dimension"), type_="check")
            batch.create_check_constraint(
                op.f("ck_embedding_profiles_fixed_dimension"), "dimension = 1536"
            )
