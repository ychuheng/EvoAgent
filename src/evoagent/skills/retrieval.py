"""小规模 Skill 的可解释 BM25 检索、过滤与选择留痕。"""

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from evoagent.core.models import ToolRisk
from evoagent.db.models import RunSkillSelectionRecord, SkillVersionRecord
from evoagent.db.unit_of_work import UnitOfWork
from evoagent.retrieval.lexical import bm25, tokenize  # noqa: F401
from evoagent.runtime.run_config import RunMode
from evoagent.skills.schema import SkillDefinition
from evoagent.tasks.lease_guard import LeaseGuard
from evoagent.tools.registry import ToolRegistry


@dataclass(frozen=True, slots=True)
class SkillDocument:
    skill_id: UUID
    version_id: UUID
    definition: SkillDefinition
    content_hash: str

    @property
    def text(self) -> str:
        return " ".join(
            (self.definition.name, self.definition.description, *self.definition.triggers)
        )


@dataclass(frozen=True, slots=True)
class RetrievalMatch:
    document: SkillDocument
    score: float
    matched_terms: tuple[str, ...]


class BM25Retriever:
    def __init__(self, *, k1: float = 1.5, b: float = 0.75) -> None:
        self._k1 = k1
        self._b = b

    def search(
        self, query: str, documents: tuple[SkillDocument, ...]
    ) -> tuple[RetrievalMatch, ...]:
        by_id = {document.version_id: document for document in documents}
        return tuple(
            RetrievalMatch(by_id[key], score, terms)
            for key, score, terms in bm25(
                query,
                {key: document.text for key, document in by_id.items()},
                k1=self._k1,
                b=self._b,
            )
        )


class SkillRetrievalService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        registry: ToolRegistry,
        *,
        top_k: int = 1,
        minimum_score: float = 0.1,
        max_risk: ToolRisk = ToolRisk.R1,
        lease_guard: LeaseGuard | None = None,
    ) -> None:
        self._lease_guard = lease_guard
        self._session_factory = session_factory
        self._registry = registry
        self._top_k = top_k
        self._minimum_score = minimum_score
        self._max_risk = max_risk
        self._retriever = BM25Retriever()

    async def select(self, run_id: UUID, goal: str) -> tuple[RetrievalMatch, ...]:
        async with UnitOfWork(self._session_factory) as unit:
            if self._lease_guard is not None:
                if self._lease_guard.lease.run_id != run_id:
                    raise ValueError("retrieval run does not match lease")
                await self._lease_guard.check(unit.session)
            run = await unit.runs.get(run_id)
            saved = tuple(
                await unit.session.scalars(
                    select(RunSkillSelectionRecord)
                    .where(RunSkillSelectionRecord.run_id == run_id)
                    .order_by(RunSkillSelectionRecord.rank)
                )
            )
            if saved:
                return tuple([await self._restore(unit, item) for item in saved])
            # 首次运行已经明确“无命中”后，配置快照就是这个负选择的锁。
            # 恢复时不能因为后来发布了新 Skill 而悄悄改变上下文。
            if run.skill_selection_frozen or run.config_snapshot is not None:
                return ()
            run.skill_selection_frozen = True
            mode = RunMode(run.run_mode)
            if mode is RunMode.BASELINE or self._top_k == 0:
                await unit.events.append(
                    run_id=run.id,
                    event_type="skill.none_selected",
                    payload={"mode": mode.value},
                    created_at=datetime.now(UTC),
                )
                await unit.commit()
                return ()
            if mode is RunMode.PINNED_SKILL:
                if run.pinned_skill_version_id is None:
                    raise ValueError("pinned run has no skill version")
                version = await unit.skill_versions.get(run.pinned_skill_version_id)
                documents = (self._document(version),)
                candidates = (RetrievalMatch(documents[0], 1.0, ("pinned",)),)
            else:
                rows = await unit.skills.active_versions()
                documents = tuple(
                    self._document(version)
                    for _, version in rows
                    if self._compatible(SkillDefinition.model_validate(version.definition))
                )
                candidates = tuple(
                    item
                    for item in self._retriever.search(goal, documents)
                    if item.score >= self._minimum_score
                )[: self._top_k]
            for rank, item in enumerate(candidates, start=1):
                unit.session.add(
                    RunSkillSelectionRecord(
                        run_id=run.id,
                        skill_version_id=item.document.version_id,
                        mode=mode.value,
                        rank=rank,
                        score=item.score,
                        query_terms=list(item.matched_terms),
                    )
                )
            await unit.events.append(
                run_id=run.id,
                event_type="skill.selected" if candidates else "skill.none_selected",
                payload={
                    "mode": mode.value,
                    "matches": [
                        {
                            "version_id": str(item.document.version_id),
                            "score": item.score,
                            "terms": item.matched_terms,
                        }
                        for item in candidates
                    ],
                },
                created_at=datetime.now(UTC),
            )
            await unit.commit()
            return candidates

    async def _restore(
        self, unit: UnitOfWork, selection: RunSkillSelectionRecord
    ) -> RetrievalMatch:
        version = await unit.skill_versions.get(selection.skill_version_id)
        return RetrievalMatch(
            self._document(version), selection.score, tuple(selection.query_terms)
        )

    @staticmethod
    def _document(version: SkillVersionRecord) -> SkillDocument:
        return SkillDocument(
            version.skill_id,
            version.id,
            SkillDefinition.model_validate(version.definition),
            version.content_hash,
        )

    def _compatible(self, definition: SkillDefinition) -> bool:
        allowed = set(definition.preconditions.allowed_tools)
        return (
            allowed <= set(self._registry.names)
            and "shell" not in allowed
            and definition.preconditions.max_effective_risk.value <= self._max_risk.value
        )
