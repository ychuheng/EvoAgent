"""事务外向量生成、提交前复验及 generation 原子切换。"""

from asyncio import timeout
from contextlib import nullcontext
from datetime import UTC
from uuid import UUID, uuid4

from sqlalchemy import delete, select

from evoagent.db.models import (
    DEFAULT_WORKSPACE_ID,
    DocumentEmbeddingRecord,
    EmbeddingProfileRecord,
    IndexGenerationRecord,
    MaintenanceJobRecord,
    RetrievalDocumentRecord,
    WorkspaceRecord,
)
from evoagent.retrieval.embeddings import EmbeddingError, EmbeddingProfile, validate
from evoagent.retrieval.sources import load_source, source_keys
from evoagent.tasks.lease_guard import database_now


async def enqueue_source(session, key):
    # 每次状态变化有独立 Job；同一个 Job 重试不重复提交向量。
    session.add(
        MaintenanceJobRecord(
            dedupe_key=f"index:{key}:{uuid4().hex}", kind="index_source", payload={"key": key}
        )
    )
    document = await session.scalar(
        select(RetrievalDocumentRecord).where(RetrievalDocumentRecord.source_key == key)
    )
    if document is not None:
        document.active = False
        await session.execute(
            delete(DocumentEmbeddingRecord).where(
                DocumentEmbeddingRecord.document_id == document.id
            )
        )


class IndexService:
    def __init__(
        self,
        factory,
        provider,
        model="mock-hash-v1",
        service_gate=None,
        dimension=1536,
        preprocessing="text-v1",
    ):
        self.factory, self.provider, self.model = factory, provider, model
        self.service_gate = service_gate
        self.dimension = dimension
        self.preprocessing = preprocessing

    async def ensure_profile(self):
        async with self.factory() as session:
            await session.scalar(
                select(WorkspaceRecord)
                .where(WorkspaceRecord.id == DEFAULT_WORKSPACE_ID)
                .with_for_update()
            )
            row = await session.scalar(
                select(EmbeddingProfileRecord).where(EmbeddingProfileRecord.model == self.model)
            )
            if row is None:
                row = EmbeddingProfileRecord(
                    model=self.model,
                    dimension=self.dimension,
                    preprocessing=self.preprocessing,
                    active_generation=1,
                    next_generation=2,
                )
                session.add(row)
                await session.flush()
                session.add(
                    IndexGenerationRecord(
                        profile_id=row.id, generation=1, status="active", manifest=[]
                    )
                )
            elif row.dimension != self.dimension or row.preprocessing != self.preprocessing:
                raise EmbeddingError("embedding profile identity changed; use a new model name")
            await session.commit()
            return row

    async def queue_rebuild(self):
        profile = await self.ensure_profile()
        async with self.factory() as session:
            profile = await session.scalar(
                select(EmbeddingProfileRecord)
                .where(EmbeddingProfileRecord.id == profile.id)
                .with_for_update()
            )
            existing_jobs = await session.scalars(
                select(MaintenanceJobRecord).where(
                    MaintenanceJobRecord.kind == "index_rebuild",
                    MaintenanceJobRecord.status.in_(("pending", "running")),
                )
            )
            for existing in existing_jobs:
                if existing.payload["profile_id"] == str(profile.id):
                    return existing
            manifest = []
            for key in await source_keys(session):
                source = await load_source(session, key)
                if source:
                    manifest.append({"key": key, "hash": source.source_hash})
            generation = profile.next_generation
            profile.next_generation += 1
            session.add(
                IndexGenerationRecord(
                    profile_id=profile.id, generation=generation, manifest=manifest
                )
            )
            job = MaintenanceJobRecord(
                dedupe_key=f"rebuild:{profile.id}:{generation}",
                kind="index_rebuild",
                payload={"profile_id": str(profile.id), "generation": generation},
            )
            session.add(job)
            await session.commit()
            return job

    async def _owned(self, session, job_id, owner, epoch):
        row = await session.scalar(
            select(MaintenanceJobRecord)
            .where(
                MaintenanceJobRecord.id == job_id,
                MaintenanceJobRecord.lease_owner == owner,
                MaintenanceJobRecord.lease_epoch == epoch,
                MaintenanceJobRecord.status == "running",
                MaintenanceJobRecord.lease_expires_at > await database_now(session),
            )
            .with_for_update()
        )
        if row is None:
            raise EmbeddingError("index_lease_lost")
        return row

    async def execute(self, job_id, owner, epoch):
        profile = await self.ensure_profile()
        async with self.factory() as session:
            job = await self._owned(session, job_id, owner, epoch)
            rebuild = job.kind == "index_rebuild"
            generation = job.payload["generation"] if rebuild else profile.active_generation
            if rebuild and job.payload["profile_id"] != str(profile.id):
                raise EmbeddingError("index_profile_mismatch")
            generation_row = await session.scalar(
                select(IndexGenerationRecord).where(
                    IndexGenerationRecord.profile_id == profile.id,
                    IndexGenerationRecord.generation == generation,
                )
            )
            keys = (
                [item["key"] for item in generation_row.manifest]
                if rebuild
                else [job.payload["key"]]
            )
            sources = []
            for key in keys:
                source = await load_source(session, key)
                if source:
                    sources.append(source)
            if len(sources) > 64:
                raise EmbeddingError("index_batch_limit")
            if rebuild and {s.key: s.source_hash for s in sources} != {
                row["key"]: row["hash"] for row in generation_row.manifest
            }:
                raise EmbeddingError("rebuild_manifest_changed")
            await session.commit()
        # 不在模型 I/O 期间持有数据库锁；整个结果先校验，部分响应不能落库。
        vectors = []
        usage = 0
        identity = EmbeddingProfile(
            profile.model, profile.dimension, profile.preprocessing, profile.metric
        )
        for offset in range(0, len(sources), 16):
            batch = sources[offset : offset + 16]

            async def check():
                async with self.factory() as session:
                    await self._owned(session, job_id, owner, epoch)

            gate = (
                self.service_gate.acquire(f"embedding:{identity.model}", check)
                if self.service_gate
                else nullcontext()
            )
            async with gate:
                await check()
                async with timeout(15):
                    result = validate(
                        await self.provider.embed(tuple(s.text[:12000] for s in batch), identity),
                        identity,
                        len(batch),
                    )
            vectors.extend(result.vectors)
            usage = usage + result.usage if usage is not None and result.usage is not None else None
        async with self.factory() as session:
            job = await self._owned(session, job_id, owner, epoch)
            current_profile = await session.scalar(
                select(EmbeddingProfileRecord)
                .where(EmbeddingProfileRecord.id == profile.id)
                .with_for_update()
            )
            if not rebuild and current_profile.active_generation != generation:
                raise EmbeddingError("index_generation_changed")
            if rebuild and current_profile.active_generation >= generation:
                raise EmbeddingError("index_generation_obsolete")
            written = 0
            for source, vector in zip(sources, vectors, strict=True):
                current = await load_source(session, source.key, lock=True)
                if (
                    current is None
                    or current.input_hash != source.input_hash
                    or current.source_hash != source.source_hash
                ):
                    continue
                document = await session.scalar(
                    select(RetrievalDocumentRecord).where(
                        RetrievalDocumentRecord.source_key == source.key
                    )
                )
                if document is None:
                    kind, raw_id = source.key.split(":")
                    foreign = {
                        "skill": "skill_version_id",
                        "memory": "memory_version_id",
                        "archive": "archive_id",
                    }[kind]
                    document = RetrievalDocumentRecord(
                        source_key=source.key,
                        **{foreign: UUID(raw_id)},
                        workspace_id=source.workspace_id,
                        session_id=source.session_id,
                        source_hash=source.source_hash,
                        input_hash=source.input_hash,
                    )
                    session.add(document)
                    await session.flush()
                document.active = True
                document.source_hash, document.input_hash = source.source_hash, source.input_hash
                existing = await session.scalar(
                    select(DocumentEmbeddingRecord.id).where(
                        DocumentEmbeddingRecord.document_id == document.id,
                        DocumentEmbeddingRecord.profile_id == profile.id,
                        DocumentEmbeddingRecord.generation == generation,
                        DocumentEmbeddingRecord.input_hash == source.input_hash,
                    )
                )
                if existing is None:
                    session.add(
                        DocumentEmbeddingRecord(
                            document_id=document.id,
                            profile_id=profile.id,
                            generation=generation,
                            input_hash=source.input_hash,
                            vector=list(vector),
                        )
                    )
                written += 1
            if rebuild:
                # 构建过程中新增/变化的合法来源不能悄悄漏入新活动代。
                expected = {s.key: s.source_hash for s in sources}
                actual = {}
                for key in await source_keys(session):
                    source = await load_source(session, key)
                    if source:
                        actual[key] = source.source_hash
                if actual != expected or written != len(expected):
                    raise EmbeddingError("rebuild_sources_changed")
                rows = await session.scalars(
                    select(IndexGenerationRecord).where(
                        IndexGenerationRecord.profile_id == profile.id
                    )
                )
                for row in rows:
                    row.status = "active" if row.generation == generation else "retired"
                current_profile.active_generation = generation
            elif not sources:
                document = await session.scalar(
                    select(RetrievalDocumentRecord).where(
                        RetrievalDocumentRecord.source_key == keys[0]
                    )
                )
                if document:
                    document.active = False
                    await session.execute(
                        delete(DocumentEmbeddingRecord).where(
                            DocumentEmbeddingRecord.document_id == document.id
                        )
                    )
            job.status = "completed"
            job.result = {"written": written, "generation": generation, "usage": usage}
            expiry = job.lease_expires_at
            if expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=UTC)
            if expiry <= await database_now(session):
                raise EmbeddingError("index_lease_lost")
            job.lease_owner = None
            job.lease_expires_at = None
            job.error_code = None
            job.next_attempt_at = None
            await session.commit()
