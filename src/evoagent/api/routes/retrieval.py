"""显式索引维护入口及只读检索证据。"""

from uuid import UUID

from fastapi import APIRouter
from sqlalchemy import select

from evoagent.api.dependencies import DatabaseDependency, SettingsDependency
from evoagent.db.models import (
    EmbeddingProfileRecord,
    RetrievalBatchRecord,
    RetrievalSelectionRecord,
)
from evoagent.memory.schema import MemoryError
from evoagent.retrieval.embeddings import provider_from_settings
from evoagent.retrieval.indexing import IndexService

router = APIRouter(tags=["retrieval"])


@router.post("/retrieval/rebuild", status_code=202)
async def rebuild(database: DatabaseDependency, settings: SettingsDependency):
    provider = provider_from_settings(settings)
    try:
        job = await IndexService(
            database.session_factory,
            provider,
            settings.embedding_model,
            dimension=settings.embedding_dimension,
            preprocessing=settings.embedding_preprocessing,
        ).queue_rebuild()
        return {"job_id": job.id, "status": job.status, "generation": job.payload["generation"]}
    finally:
        if hasattr(provider, "aclose"):
            await provider.aclose()


@router.get("/retrieval/profiles")
async def profiles(database: DatabaseDependency):
    async with database.session_factory() as session:
        return [
            {
                "id": p.id,
                "model": p.model,
                "dimension": p.dimension,
                "active_generation": p.active_generation,
                "metric": p.metric,
                "preprocessing": p.preprocessing,
            }
            for p in await session.scalars(select(EmbeddingProfileRecord))
        ]


@router.get("/runs/{run_id}/retrieval")
async def evidence(run_id: UUID, database: DatabaseDependency):
    async with database.session_factory() as session:
        batch = await session.scalar(
            select(RetrievalBatchRecord).where(RetrievalBatchRecord.run_id == run_id)
        )
        if batch is None:
            raise MemoryError("retrieval_batch_not_found")
        rows = await session.scalars(
            select(RetrievalSelectionRecord).where(RetrievalSelectionRecord.batch_id == batch.id)
        )
        return {
            "batch_id": batch.id,
            "config": batch.config,
            "generation": batch.generation,
            "degraded": batch.degraded,
            "selected_count": batch.selected_count,
            "selections": [
                {
                    "source_key": row.source_key,
                    "source_hash": row.source_hash,
                    "text_hash": row.text_hash,
                    "rank": row.rank,
                    "omission_reason": row.omission_reason,
                    "evidence": row.evidence,
                }
                for row in rows
            ],
        }
