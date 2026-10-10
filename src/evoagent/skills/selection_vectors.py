"""Optional vector scores for already-authorized and applicable v3 candidates."""

import asyncio
from contextlib import nullcontext
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from evoagent.db.models import EmbeddingProfileRecord, RetrievalDocumentRecord
from evoagent.retrieval.embeddings import (
    EmbeddingError,
    EmbeddingProfile,
    provider_from_settings,
    validate,
)
from evoagent.retrieval.vector import exact_distances
from evoagent.sessions.service import text_hash
from evoagent.workers.rate_limit import RateLimited


@dataclass(frozen=True)
class SkillVectorScores:
    distances: dict
    profile_id: UUID | None = None
    generation: int | None = None
    degraded: str | None = None
    usage: int | None = 0


async def vector_scores(selector, goal, candidates, *, facts, provider=None, service_gate=None):
    eligible = {
        f"skill:{item.document.version_id}": item
        for item in candidates
        if item.scope == selector.scope
        and selector.assess(item.document.definition, facts).status == "applicable"
    }
    if not eligible:
        return SkillVectorScores({})
    async with selector.factory() as session:
        profile = await session.scalar(
            select(EmbeddingProfileRecord).where(
                EmbeddingProfileRecord.model == selector.settings.embedding_model
            )
        )
        if (
            profile is None
            or profile.active_generation <= 0
            or profile.dimension != selector.settings.embedding_dimension
            or profile.preprocessing != selector.settings.embedding_preprocessing
            or profile.metric != "cosine"
        ):
            return SkillVectorScores({}, degraded="index_not_ready")
        rows = tuple(
            await session.scalars(
                select(RetrievalDocumentRecord).where(
                    RetrievalDocumentRecord.source_key.in_(eligible),
                    RetrievalDocumentRecord.active.is_(True),
                )
            )
        )
        allowed = {
            row.id: eligible[row.source_key].document.version_id
            for row in rows
            if row.source_hash == eligible[row.source_key].document.content_hash
            and row.input_hash == text_hash(eligible[row.source_key].document.text[:12000])
            and row.workspace_id in (None, selector.scope.workspace_id)
        }
        profile_id, generation = profile.id, profile.active_generation
        identity = EmbeddingProfile(
            profile.model, profile.dimension, profile.preprocessing, profile.metric
        )
    if not allowed:
        return SkillVectorScores({}, profile_id, generation, "index_not_ready")
    owned_provider, usage = provider is None, None
    try:
        provider = provider or provider_from_settings(selector.settings)

        async def check():
            async with selector.factory() as session:
                await selector.guard.check(session)

        # Waiting, model inference and vector IO never hold task row locks.
        async with asyncio.timeout(15):
            gate = (
                service_gate.acquire(f"embedding:{identity.model}", check)
                if service_gate
                else nullcontext()
            )
            async with gate:
                await check()
                result = validate(await provider.embed((goal[:12000],), identity), identity, 1)
                usage = result.usage
            await check()
            async with selector.factory() as session:
                scores = await exact_distances(
                    session,
                    profile_id=profile_id,
                    generation=generation,
                    allowed_documents=tuple(allowed),
                    vector=result.vectors[0],
                )
        # Unindexed or unavailable vectors keep lexical candidates; scores can
        # never introduce another version or grant its source/scope authority.
        distances = {allowed[key]: value for key, value in scores.items() if key in allowed}
        degraded = "partial_index" if len(distances) < len(eligible) else None
        return SkillVectorScores(distances, profile_id, generation, degraded, usage)
    except (EmbeddingError, TimeoutError, OSError, SQLAlchemyError, RateLimited):
        return SkillVectorScores({}, profile_id, generation, "vector_unavailable", usage)
    finally:
        if owned_provider and provider is not None and hasattr(provider, "aclose"):
            await provider.aclose()
