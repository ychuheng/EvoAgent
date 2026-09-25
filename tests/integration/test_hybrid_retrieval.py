from datetime import UTC, datetime, timedelta

import pytest
import test_memory_foundations as foundations
from sqlalchemy import select
from test_memory_foundations import confirm, proposed

from evoagent.config import Settings
from evoagent.core.context import ContextBuilder
from evoagent.db.models import (
    DocumentEmbeddingRecord,
    EmbeddingProfileRecord,
    MaintenanceJobRecord,
    RetrievalBatchRecord,
    RetrievalSelectionRecord,
)
from evoagent.memory.maintenance import MaintenanceWorker
from evoagent.memory.schema import MemoryDecision, MemoryError
from evoagent.retrieval.embeddings import EmbeddingError, MockEmbeddingProvider
from evoagent.retrieval.indexing import IndexService
from evoagent.runtime.context_resolver import ContextResolver
from evoagent.runtime.run_config import RunMode
from evoagent.tasks.lease import JobLeaseManager
from evoagent.tasks.lease_guard import LeaseGuard
from evoagent.tools.registry import ToolRegistry

env = foundations.env


async def indexed(environment):
    db, _, sid, store = environment
    service, entry, version, _, _ = await proposed(environment)
    entry, version = await confirm(service, sid, entry, version)
    index = IndexService(db.session_factory, MockEmbeddingProvider())
    worker = MaintenanceWorker(db.session_factory, store, index)
    assert await worker.run_once()
    return service, entry, version, index, worker


async def resolver(environment, tmp_path, goal="中文回答", **kwargs):
    db, tasks, sid, _ = environment
    task = await tasks.create_task(session_id=sid, goal=goal, provider="mock", model="mock")
    lease = await JobLeaseManager(db.session_factory, lease_seconds=60).claim_next("retrieval")
    settings = Settings(
        workspace=tmp_path / "workspace",
        retrieval_backend="hybrid",
        memory_retrieval_enabled=True,
        **kwargs,
    )
    return task, ContextResolver(
        db.session_factory, settings, ToolRegistry([]), LeaseGuard(lease), ContextBuilder()
    )


async def test_index_job_and_frozen_context_restore_without_provider(env, tmp_path):
    db, _, _, _ = env
    _, _, _, index, _ = await indexed(env)
    task, resolve = await resolver(env, tmp_path)
    first = await resolve.resolve(task.task, task.run)
    assert len(first.memory_texts) == 1
    assert first.config["generation"] == 1
    rebuild = await index.queue_rebuild()
    assert rebuild.payload["generation"] == 2

    class Fail:
        async def embed(self, *_args):
            raise AssertionError("restore must not query embedding")

    resolve.provider = Fail()
    assert await resolve.resolve(task.task, task.run) == first
    async with db.session_factory() as session:
        assert len(tuple(await session.scalars(select(RetrievalBatchRecord)))) == 1


async def test_native_dimension_profile_is_indexed_without_padding(env, tmp_path):
    db, _, sid, store = env
    service, entry, version, _, _ = await proposed(env)
    await confirm(service, sid, entry, version)
    index = IndexService(db.session_factory, MockEmbeddingProvider(), dimension=384)
    worker = MaintenanceWorker(db.session_factory, store, index)
    assert await worker.run_once()
    async with db.session_factory() as session:
        profile = await session.scalar(select(EmbeddingProfileRecord))
        vector = await session.scalar(select(DocumentEmbeddingRecord))
        assert profile.dimension == len(vector.vector) == 384
    task, resolve = await resolver(env, tmp_path, embedding_dimension=384)
    result = await resolve.resolve(task.task, task.run)
    assert len(result.memory_texts) == 1
    with pytest.raises(EmbeddingError, match="profile identity changed"):
        await IndexService(db.session_factory, MockEmbeddingProvider()).ensure_profile()


async def test_partition_budget_records_non_injection(env, tmp_path):
    db, _, _, _ = env
    await indexed(env)
    task, resolve = await resolver(env, tmp_path, retrieval_memory_budget=0)
    result = await resolve.resolve(task.task, task.run)
    assert result.memory_texts == ()
    async with db.session_factory() as session:
        selection = await session.scalar(select(RetrievalSelectionRecord))
        assert selection.omission_reason == "partition_budget" and selection.text is None


async def test_missing_or_failed_vector_falls_back_to_filtered_lexical(env, tmp_path):
    await indexed(env)
    task, resolve = await resolver(env, tmp_path)

    class Fail:
        async def embed(self, *_args):
            raise EmbeddingError("network timeout")

    resolve.provider = Fail()
    result = await resolve.resolve(task.task, task.run)
    assert len(result.memory_texts) == 1 and result.config["degraded"] == "vector_unavailable"


async def test_zero_hit_is_frozen_after_later_confirmation(env, tmp_path):
    task, resolve = await resolver(env, tmp_path)
    first = await resolve.resolve(task.task, task.run)
    assert first.memory_texts == ()
    # 在当前 Run 之后发布的记忆不能改变已冻结负选择。
    await indexed(env)
    assert await resolve.resolve(task.task, task.run) == first


async def test_revocation_during_embedding_cannot_reactivate_vector(env):
    db, _, sid, store = env
    service, entry, version, _, _ = await proposed(env)
    entry, version = await confirm(service, sid, entry, version)

    class Revoke(MockEmbeddingProvider):
        async def embed(self, texts, profile):
            await service.decide(
                sid,
                version.id,
                MemoryDecision(action="revoke", expected_lock_version=entry.lock_version),
            )
            return await super().embed(texts, profile)

    worker = MaintenanceWorker(
        db.session_factory, store, IndexService(db.session_factory, Revoke())
    )
    await worker.run_once()
    async with db.session_factory() as session:
        assert await session.scalar(select(DocumentEmbeddingRecord)) is None


async def test_rebuild_switches_only_after_success(env):
    db, _, _, _ = env
    _, _, _, index, worker = await indexed(env)
    job = await index.queue_rebuild()
    async with db.session_factory() as session:
        assert (await session.scalar(select(EmbeddingProfileRecord))).active_generation == 1
    await worker.run_once()
    async with db.session_factory() as session:
        assert (await session.scalar(select(EmbeddingProfileRecord))).active_generation == 2
        assert (await session.get(MaintenanceJobRecord, job.id)).status == "completed"


async def test_old_index_epoch_rejected(env):
    db, _, _, store = env
    _, _, _, index, worker = await indexed(env)
    await index.queue_rebuild()
    lease = await worker.claim()
    async with db.session_factory() as session:
        job = await session.get(MaintenanceJobRecord, lease[0])
        job.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await session.commit()
    new = MaintenanceWorker(db.session_factory, store, index)
    replacement = await new.claim()
    with pytest.raises(EmbeddingError, match="index_lease_lost"):
        await index.execute(lease[0], worker.owner, lease[1])
    await new.execute(*replacement)


async def test_frozen_memory_is_invalid_after_revoke(env, tmp_path):
    db, _, sid, _ = env
    service, entry, version, _, _ = await indexed(env)
    task, resolve = await resolver(env, tmp_path)
    await resolve.resolve(task.task, task.run)
    await service.decide(
        sid, version.id, MemoryDecision(action="revoke", expected_lock_version=entry.lock_version)
    )
    with pytest.raises(MemoryError, match="context_source_revoked"):
        await resolve.resolve(task.task, task.run)
    async with db.session_factory() as session:
        assert await session.scalar(select(DocumentEmbeddingRecord)) is None


async def test_index_failure_has_no_partial_vectors(env):
    db, _, sid, store = env
    service, entry, version, _, _ = await proposed(env)
    await confirm(service, sid, entry, version)

    class Bad:
        async def embed(self, *_):
            from evoagent.retrieval.embeddings import EmbeddingResult

            return EmbeddingResult((), "mock-hash-v1")

    worker = MaintenanceWorker(db.session_factory, store, IndexService(db.session_factory, Bad()))
    await worker.run_once()
    async with db.session_factory() as session:
        assert await session.scalar(select(DocumentEmbeddingRecord)) is None
        job = await session.scalar(select(MaintenanceJobRecord))
        assert job.status == "failed" and job.next_attempt_at is not None


async def test_rebuild_failure_keeps_active_generation(env):
    db, _, _, _ = env
    _, _, _, index, worker = await indexed(env)
    job = await index.queue_rebuild()

    class Fail:
        async def embed(self, *_):
            raise EmbeddingError("unavailable")

    index.provider = Fail()
    await worker.run_once()
    async with db.session_factory() as session:
        assert (await session.scalar(select(EmbeddingProfileRecord))).active_generation == 1
        assert (await session.get(MaintenanceJobRecord, job.id)).status == "failed"


async def test_cross_session_memory_never_reaches_embedding(env, tmp_path):
    db, tasks, _, store = env
    await indexed(env)
    scope = await tasks.create_session("other")
    task, resolve = await resolver((db, tasks, scope.id, store), tmp_path)

    class Forbidden:
        async def embed(self, *_):
            raise AssertionError("no authorized candidates")

    resolve.provider = Forbidden()
    result = await resolve.resolve(task.task, task.run)
    assert not result.memory_texts


@pytest.mark.parametrize("mode", ["retrieval", "baseline"])
async def test_runner_injects_only_opted_in_ordinary_memory(env, tmp_path, mode):
    from test_persistent_runtime import response

    from evoagent.providers.mock import MockProvider
    from evoagent.runtime.persistent_runner import PersistentAgentRunner
    from evoagent.tasks.state_machine import PersistentRunStatus

    db, tasks, sid, _ = env
    await indexed(env)
    await tasks.create_task(
        session_id=sid, goal="中文回答", provider="mock", model="mock", run_mode=RunMode(mode)
    )
    lease = await JobLeaseManager(db.session_factory, lease_seconds=60).claim_next("integration")
    provider = MockProvider([response("完成")])
    result = await PersistentAgentRunner(
        settings=Settings(workspace=tmp_path / "workspace", memory_retrieval_enabled=True),
        session_factory=db.session_factory,
        context_builder=ContextBuilder(),
        provider=provider,
        registry=ToolRegistry([]),
    ).handle(lease)
    assert result.status == PersistentRunStatus.COMPLETED
    injected = [m for m in provider.requests[0].messages if m.content and "长期记忆" in m.content]
    assert bool(injected) == (mode == "retrieval")
    assert all(m.role.value == "user" for m in injected)


async def test_archive_erase_invalidates_frozen_context_and_vectors(env, tmp_path):
    from evoagent.db.models import RetrievalDocumentRecord, RunSnapshotRecord, SessionArchiveRecord
    from evoagent.memory.archival import enqueue_archive
    from evoagent.memory.repository import check_run_references

    db, _, sid, _ = env
    service, entry, version, _, worker = await indexed(env)
    await enqueue_archive(db.session_factory, sid)
    await worker.run_once()
    await worker.run_once()
    task, resolve = await resolver(env, tmp_path, archive_retrieval_enabled=True)
    resolve.settings = resolve.settings.model_copy(update={"memory_retrieval_enabled": False})
    result = await resolve.resolve(task.task, task.run)
    assert any("会话归档" in item for item in result.memory_texts)
    async with db.session_factory() as session:
        session.add(
            RunSnapshotRecord(
                run_id=task.run.id, schema_version=2, state={"private": "derived"}, event_sequence=1
            )
        )
        await session.commit()
    await service.decide(
        sid, version.id, MemoryDecision(action="erase", expected_lock_version=entry.lock_version)
    )
    while await worker.run_once():
        pass
    async with db.session_factory() as session:
        assert (await session.scalar(select(SessionArchiveRecord))).status == "erased"
        assert all(
            row.text is None for row in await session.scalars(select(RetrievalSelectionRecord))
        )
        archive_doc = await session.scalar(
            select(RetrievalDocumentRecord).where(RetrievalDocumentRecord.archive_id.is_not(None))
        )
        assert not archive_doc.active
        assert (await session.scalar(select(RunSnapshotRecord))).state == {"erased": True}
        with pytest.raises(MemoryError, match="context_source_revoked"):
            await check_run_references(session, task.run.id)


async def test_profile_identity_cannot_be_rewritten(env):
    db, _, _, _ = env
    await indexed(env)
    async with db.session_factory() as session:
        profile = await session.scalar(select(EmbeddingProfileRecord))
        profile.model = "another-space"
        with pytest.raises(ValueError, match="immutable record fields"):
            await session.commit()


async def test_retrieval_api_reads_evidence_without_writes_and_rebuild_is_explicit(env, tmp_path):
    import httpx

    from evoagent.api.app import create_app

    db, _, _, _ = env
    await indexed(env)
    task, resolve = await resolver(env, tmp_path)
    await resolve.resolve(task.task, task.run)
    app = create_app(Settings(workspace=tmp_path / "workspace"), database=db)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client,
    ):
        async with db.session_factory() as session:
            jobs_before = list(await session.scalars(select(MaintenanceJobRecord.id)))
        response = await client.get(f"/api/v1/runs/{task.run.id}/retrieval")
        assert response.status_code == 200
        assert response.json()["selected_count"] == 1
        assert "text" not in response.json()["selections"][0]
        assert (await client.get("/api/v1/retrieval/profiles")).json()[0]["dimension"] == 1536
        async with db.session_factory() as session:
            assert list(await session.scalars(select(MaintenanceJobRecord.id))) == jobs_before
        first = await client.post("/api/v1/retrieval/rebuild")
        second = await client.post("/api/v1/retrieval/rebuild")
        assert first.status_code == second.status_code == 202
        assert first.json()["job_id"] == second.json()["job_id"]
