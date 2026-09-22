"""短事务取候选、事务外向量请求、复验后冻结实际注入文本。"""

from asyncio import timeout
from contextlib import nullcontext
from dataclasses import dataclass
from time import perf_counter
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from evoagent.core.context_budget import ConservativeTokenCounter
from evoagent.core.context_policy import policy_from_settings
from evoagent.core.models import Message, MessageRole, ModelRequest
from evoagent.db.models import (
    EmbeddingProfileRecord,
    RetrievalBatchRecord,
    RetrievalDocumentRecord,
    RetrievalSelectionRecord,
    RunSkillSelectionRecord,
    SessionRecord,
    SkillRecord,
    SkillVersionRecord,
    utc_now,
)
from evoagent.db.unit_of_work import UnitOfWork
from evoagent.memory.repository import bind_version, check_run_references
from evoagent.memory.schema import MemoryError
from evoagent.retrieval.embeddings import (
    EmbeddingError,
    EmbeddingProfile,
    provider_from_settings,
    validate,
)
from evoagent.retrieval.hybrid import ROUTE_TOP_N, rank
from evoagent.retrieval.sources import load_source, source_keys
from evoagent.retrieval.vector import exact_distances
from evoagent.runtime.checkpoints import SnapshotCompatibilityError
from evoagent.sessions.service import text_hash
from evoagent.skills.canonical import content_hash
from evoagent.skills.retrieval import RetrievalMatch, SkillRetrievalService
from evoagent.workers.rate_limit import RateLimited


@dataclass(frozen=True)
class ResolvedContext:
    skills: tuple
    skill_text: str | None
    memory_texts: tuple[str, ...]
    config: dict


class ContextResolver:
    def __init__(
        self, factory, settings, registry, guard, builder, provider=None, service_gate=None
    ):
        self.factory, self.settings, self.registry = factory, settings, registry
        self.guard, self.builder = guard, builder
        self.provider = provider
        self.service_gate = service_gate

    def config(self):
        s = self.settings
        return {
            "algorithm": "bm25-rrf-v1",
            "route_top_n": ROUTE_TOP_N,
            "backend": s.retrieval_backend,
            "model": s.embedding_model,
            "memory": s.memory_retrieval_enabled,
            "archive": s.archive_retrieval_enabled,
            "rrf_k": s.retrieval_rrf_k,
            "lexical_threshold": s.retrieval_min_lexical_score,
            "distance_threshold": s.retrieval_max_vector_distance,
            "skill_top_k": s.skill_retrieval_top_k,
            "memory_top_k": s.memory_retrieval_top_k,
            "skill_budget": s.retrieval_skill_budget,
            "memory_budget": s.retrieval_memory_budget,
        }

    async def resolve(self, task, run):
        config = self.config()
        config["history_before_sequence"] = task.history_before_sequence
        async with self.factory() as session:
            saved = await session.scalar(
                select(RetrievalBatchRecord).where(RetrievalBatchRecord.run_id == run.id)
            )
            if saved:
                return await self._restore(session, saved, config)
            scope = await session.get(SessionRecord, task.session_id)
            candidates = {}
            for key in await source_keys(session):
                kind = key.split(":")[0]
                if (
                    kind == "memory"
                    and not config["memory"]
                    or kind == "archive"
                    and not config["archive"]
                ):
                    continue
                source = await load_source(
                    session,
                    key,
                    scope_id=scope.id,
                    cutoff=task.history_before_sequence,
                    registry=self.registry,
                    max_risk=self.settings.skill_max_effective_risk.value,
                )
                if source:
                    candidates[key] = source
            profile = await session.scalar(
                select(EmbeddingProfileRecord).where(
                    EmbeddingProfileRecord.model == config["model"]
                )
            )
            profile_id = profile.id if profile else None
            generation = profile.active_generation if profile else None
            docs = tuple(
                await session.scalars(
                    select(RetrievalDocumentRecord).where(
                        RetrievalDocumentRecord.source_key.in_(candidates),
                        RetrievalDocumentRecord.active.is_(True),
                    )
                )
            )
            allowed = {
                doc.id: doc.source_key
                for doc in docs
                if doc.source_hash == candidates[doc.source_key].source_hash
                and doc.input_hash == candidates[doc.source_key].input_hash
            }
        distances, degraded = {}, None
        embedding_usage = 0
        retrieval_started = perf_counter()
        if config["backend"] == "hybrid" and candidates:
            if not profile or not allowed:
                degraded = "index_not_ready"
            else:
                provider = self.provider
                try:
                    provider = provider or provider_from_settings(self.settings)
                    identity = EmbeddingProfile(
                        profile.model, profile.dimension, profile.preprocessing, profile.metric
                    )
                    async with timeout(15):

                        async def check():
                            async with self.factory() as session:
                                await self.guard.check(session)

                        gate = (
                            self.service_gate.acquire(f"embedding:{identity.model}", check)
                            if self.service_gate
                            else nullcontext()
                        )
                        async with gate:
                            await check()
                            embedding_usage = None
                            result = validate(
                                await provider.embed((task.goal[:12000],), identity), identity, 1
                            )
                            embedding_usage = result.usage
                    async with self.factory() as session:
                        rows = await exact_distances(
                            session,
                            profile_id=profile_id,
                            generation=generation,
                            allowed_documents=tuple(allowed),
                            vector=result.vectors[0],
                        )
                    distances = {allowed[key]: value for key, value in rows.items()}
                    if len(rows) < len(candidates):
                        degraded = "partial_index"
                except (EmbeddingError, TimeoutError, OSError, SQLAlchemyError, RateLimited):
                    degraded = "vector_unavailable"
                finally:
                    if (
                        self.provider is None
                        and provider is not None
                        and hasattr(provider, "aclose")
                    ):
                        await provider.aclose()
        ranked = rank(
            task.goal,
            {key: s.text for key, s in candidates.items()},
            distances,
            minimum_score=config["lexical_threshold"],
            maximum_distance=config["distance_threshold"],
            rrf_k=config["rrf_k"],
        )
        async with UnitOfWork(self.factory) as unit:
            await self.guard.check(unit.session)
            saved = await unit.session.scalar(
                select(RetrievalBatchRecord).where(RetrievalBatchRecord.run_id == run.id)
            )
            if saved:
                return await self._restore(unit.session, saved, config)
            batch = RetrievalBatchRecord(
                run_id=run.id,
                purpose="context",
                query_hash=text_hash(task.goal),
                workspace_id=scope.workspace_id,
                session_id=scope.id,
                profile_id=profile_id,
                generation=generation,
                config=config,
                degraded=degraded,
                selected_count=0,
            )
            unit.session.add(batch)
            await unit.session.flush()
            chosen_skills, chosen_memories = [], []
            spent = {"skill": 0, "memory": 0}
            counter = ConservativeTokenCounter()
            for key, evidence in ranked:
                source = candidates[key]
                current = await load_source(
                    unit.session,
                    key,
                    scope_id=scope.id,
                    cutoff=task.history_before_sequence,
                    registry=self.registry,
                    max_risk=self.settings.skill_max_effective_risk.value,
                    lock=True,
                )
                if current is None or current.source_hash != source.source_hash:
                    continue
                kind = "skill" if key.startswith("skill:") else "memory"
                chosen = chosen_skills if kind == "skill" else chosen_memories
                cost = counter.count_request(
                    ModelRequest(
                        model=run.model,
                        messages=(Message(role=MessageRole.USER, content=source.rendered),),
                    )
                ).count
                reason = None
                if len(chosen) >= config[f"{kind}_top_k"]:
                    reason = "top_k"
                elif spent[kind] + cost > config[f"{kind}_budget"]:
                    reason = "partition_budget"
                else:
                    skills = [s.rendered for s in chosen_skills] + (
                        [source.rendered] if kind == "skill" else []
                    )
                    memories = [s.rendered for s in chosen_memories] + (
                        [source.rendered] if kind != "skill" else []
                    )
                    messages = self.builder.build(
                        task.goal,
                        skill_context="\n\n".join(skills) or None,
                        external_context=memories,
                    )
                    policy = policy_from_settings(self.settings)
                    request = ModelRequest(
                        model=run.model,
                        messages=messages,
                        tool_definitions=self.registry.definitions(),
                    )
                    if (
                        hasattr(policy, "budget")
                        and policy.counter.count_request(request).count > policy.budget.input_limit
                    ):
                        reason = "request_budget"
                if reason is None:
                    spent[kind] += cost
                    chosen.append(source)
                    batch.selected_count += 1
                    if key.startswith("memory:"):
                        await bind_version(unit.session, run.id, UUID(key.split(":")[1]))
                    if kind == "skill":
                        unit.session.add(
                            RunSkillSelectionRecord(
                                run_id=run.id,
                                skill_version_id=UUID(key.split(":")[1]),
                                mode="retrieval",
                                rank=len(chosen),
                                score=evidence["rrf"],
                                query_terms=evidence["terms"],
                            )
                        )
                unit.session.add(
                    RetrievalSelectionRecord(
                        batch_id=batch.id,
                        source_key=key,
                        source_hash=source.source_hash,
                        text_hash=text_hash(source.rendered),
                        text=source.rendered if reason is None else None,
                        evidence=evidence,
                        rank=batch.selected_count if reason is None else None,
                        omission_reason=reason,
                    )
                )
            owned_run = await unit.runs.get(run.id)
            owned_run.skill_selection_frozen = True
            await unit.events.append(
                run_id=run.id,
                event_type="retrieval.degraded" if degraded else "retrieval.frozen",
                payload={
                    "batch_id": str(batch.id),
                    "selected_count": batch.selected_count,
                    "degraded": degraded,
                    "embedding_tokens": embedding_usage,
                    "latency_ms": (perf_counter() - retrieval_started) * 1000,
                },
                created_at=utc_now(),
            )
            await unit.commit()
            return await self._restore(unit.session, batch, config)

    async def _restore(self, session, batch, config):
        if batch.config != config:
            raise SnapshotCompatibilityError("retrieval configuration changed")
        await check_run_references(session, batch.run_id)
        selections = tuple(
            await session.scalars(
                select(RetrievalSelectionRecord)
                .where(
                    RetrievalSelectionRecord.batch_id == batch.id,
                    RetrievalSelectionRecord.omission_reason.is_(None),
                )
                .order_by(RetrievalSelectionRecord.rank)
            )
        )
        matches, skills, memories = [], [], []
        for row in selections:
            if row.text is None or text_hash(row.text) != row.text_hash:
                raise MemoryError("context_source_revoked")
            if row.source_key.startswith("skill:"):
                version = await session.get(SkillVersionRecord, UUID(row.source_key.split(":")[1]))
                skill = await session.get(SkillRecord, version.skill_id)
                if (
                    skill.status.value != "enabled"
                    or content_hash(version.definition) != row.source_hash
                ):
                    raise MemoryError("context_source_revoked")
                matches.append(
                    RetrievalMatch(
                        SkillRetrievalService._document(version),
                        row.evidence["rrf"],
                        tuple(row.evidence["terms"]),
                    )
                )
                skills.append(row.text)
            else:
                source = await load_source(
                    session,
                    row.source_key,
                    scope_id=batch.session_id,
                    cutoff=batch.config["history_before_sequence"],
                )
                if source is None or source.source_hash != row.source_hash:
                    raise MemoryError("context_source_revoked")
                memories.append(row.text)
        frozen_config = {
            **config,
            "profile_id": str(batch.profile_id) if batch.profile_id else None,
            "generation": batch.generation,
            "degraded": batch.degraded,
            "selection_hash": content_hash([(s.source_key, s.text_hash) for s in selections]),
        }
        return ResolvedContext(
            tuple(matches), "\n\n".join(skills) or None, tuple(memories), frozen_config
        )
