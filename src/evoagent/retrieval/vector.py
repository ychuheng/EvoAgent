"""PostgreSQL 固定 vector(1536)，SQLite JSON 仅为功能测试替身。"""

import json
import math

from sqlalchemy import JSON, Float, String, bindparam, cast, select
from sqlalchemy.dialects.postgresql.base import ischema_names
from sqlalchemy.types import UserDefinedType

from evoagent.retrieval.embeddings import DIMENSION


class PGVector(UserDefinedType):
    cache_ok = True

    def __init__(self, dimension=1536):
        if int(dimension) != DIMENSION:
            raise ValueError("unsupported vector dimension")

    def get_col_spec(self, **kwargs):
        return f"VECTOR({DIMENSION})"

    def bind_processor(self, dialect):
        return lambda value: json.dumps(value) if value is not None else None

    def result_processor(self, dialect, coltype):
        return lambda value: json.loads(value) if isinstance(value, str) else value


VECTOR = PGVector().with_variant(JSON(), "sqlite")

ischema_names["vector"] = PGVector


def cosine_distance(left, right):
    numerator = sum(a * b for a, b in zip(left, right, strict=True))
    denominator = math.sqrt(sum(a * a for a in left) * sum(b * b for b in right))
    return 1 - numerator / denominator


async def exact_distances(session, *, profile_id, generation, allowed_documents, vector):
    from evoagent.db.models import DocumentEmbeddingRecord, RetrievalDocumentRecord

    if not allowed_documents:
        return {}
    table = DocumentEmbeddingRecord
    conditions = (
        table.profile_id == profile_id,
        table.generation == generation,
        table.document_id.in_(allowed_documents),
        table.document_id == RetrievalDocumentRecord.id,
        table.input_hash == RetrievalDocumentRecord.input_hash,
        RetrievalDocumentRecord.active.is_(True),
    )
    if session.bind.dialect.name == "postgresql":
        distance = table.vector.op("<=>", return_type=Float)(
            cast(bindparam("query_vector", json.dumps(vector), type_=String), PGVector())
        )
        rows = await session.execute(
            select(table.document_id, distance.label("distance"))
            .where(*conditions)
            .order_by(distance, table.document_id)
        )
        return {row[0]: float(row[1]) for row in rows}
    rows = await session.scalars(select(table).where(*conditions))
    return {row.document_id: cosine_distance(row.vector, vector) for row in rows}
