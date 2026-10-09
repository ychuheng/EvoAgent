import asyncio

from sqlalchemy import select

from evoagent.db.models import (
    LearningRequestRecord,
    LearningSourceRecord,
    SkillRecord,
    SkillVersionRecord,
)
from evoagent.learning.demo import PersonalDemoGenerator
from evoagent.learning.worker import LearningJobHandler
from evoagent.memory.maintenance import MaintenanceWorker
from evoagent.skills.extraction import MockCandidateGenerator
from evoagent.skills.lifecycle import SkillVersionStatus
from evoagent.skills.validation import SkillDefinitionValidator
from evoagent.tools.catalog import default_skill_tool_catalog
from evoagent.trace.artifacts import LocalArtifactStore
from tests.integration.test_learning_api import enable

pytest_plugins = ("tests.integration.test_learning_api",)


def worker(db, settings, generator=None):
    store = LocalArtifactStore(settings.artifact_root)
    validator = SkillDefinitionValidator(
        default_skill_tool_catalog(),
        allowed_tools=frozenset({"file_read"}),
        supported_schema_version=2,
    )
    handler = LearningJobHandler(
        db.session_factory,
        store,
        lambda request, guard: generator or PersonalDemoGenerator(),
        validator,
        learning_enabled=True,
    )
    return MaintenanceWorker(
        db.session_factory,
        store,
        handlers={
            "learning_propose": handler,
            "learning_validate": handler,
            "learning_revoke": handler,
        },
        allowed_kinds={"learning_propose", "learning_validate", "learning_revoke"},
    )


async def submit(client, run_id, key="manual"):
    await enable(client)
    response = await client.post(
        f"/api/v1/runs/{run_id}/learning-requests", json={"client_request_id": key}
    )
    assert response.status_code == 202, response.text
    return response.json()


async def test_personal_task_to_frozen_source_draft_and_review_is_resumable(learning_api):
    client, db, run_id, settings = learning_api
    request = await submit(client, run_id)
    # A different worker owns each durable stage, simulating restarts.
    assert await worker(db, settings).run_once()
    assert await worker(db, settings).run_once()
    assert await worker(db, settings).run_once()
    assert not await worker(db, settings).run_once()
    response = await client.get(f"/api/v1/learning-requests/{request['id']}")
    candidate = response.json()
    assert candidate["status"] == "ready_for_review"
    assert candidate["validation_report"]["task_validation"]["status"] == "not_run"
    assert not candidate["validation_report"]["trial_eligible"]
    from uuid import UUID

    async with db.session_factory() as session:
        version = await session.get(SkillVersionRecord, UUID(candidate["candidate_version_id"]))
        skill = await session.get(SkillRecord, version.skill_id)
        assert version.lifecycle_status is SkillVersionStatus.DRAFT
        assert skill.active_version_id is None
        assert len(list(await session.scalars(select(SkillVersionRecord)))) == 1
    reviewed = await client.post(
        f"/api/v1/learning-requests/{request['id']}/review",
        json={
            "expected_lock_version": candidate["lock_version"],
            "reason": "reviewed evidence",
            "action": "acknowledge",
        },
    )
    assert reviewed.status_code == 200, reviewed.text
    assert reviewed.json()["status"] == "completed"


async def test_static_invalid_candidate_fails_without_auto_retry(learning_api):
    client, db, run_id, settings = learning_api
    request = await submit(client, run_id)
    runner = worker(db, settings, MockCandidateGenerator(ValueError("secret-not-for-error")))
    assert await runner.run_once()
    assert await runner.run_once()
    assert not await runner.run_once()
    response = await client.get(f"/api/v1/learning-requests/{request['id']}")
    assert response.json()["status"] == "failed"
    assert "secret-not-for-error" not in response.text
    async with db.session_factory() as session:
        assert not list(await session.scalars(select(SkillVersionRecord)))


async def test_cancellation_during_generation_drops_late_result(learning_api):
    client, db, run_id, settings = learning_api
    request = await submit(client, run_id)
    started, closed = asyncio.Event(), asyncio.Event()

    class Blocked:
        async def generate(self, sources, *, context=None):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                closed.set()

    runner = worker(db, settings, Blocked())
    assert await runner.run_once()
    pending = asyncio.create_task(runner.run_once())
    try:
        await asyncio.wait_for(started.wait(), 5)
        state = (await client.get(f"/api/v1/learning-requests/{request['id']}")).json()
        cancelled = await client.post(
            f"/api/v1/learning-requests/{request['id']}/cancel",
            json={"expected_lock_version": state["lock_version"]},
        )
        assert cancelled.status_code == 200
        await asyncio.wait_for(closed.wait(), 2)
        assert await asyncio.wait_for(pending, 3)
        async with db.session_factory() as session:
            assert not list(await session.scalars(select(SkillVersionRecord)))
    finally:
        pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)


async def test_revocation_cleanup_does_not_rewrite_history(learning_api):
    client, db, run_id, settings = learning_api
    request = await submit(client, run_id)
    runner = worker(db, settings)
    for _ in range(3):
        assert await runner.run_once()
    async with db.session_factory() as session:
        source = await session.scalar(select(LearningSourceRecord))
        source_id = source.id
        version = await session.scalar(select(SkillVersionRecord))
        original = version.definition
    revoked = await client.post(
        f"/api/v1/learning-sources/{source_id}/revoke",
        json={"reason": "withdraw", "expected_status": "valid"},
    )
    assert revoked.status_code == 200
    assert await runner.run_once()
    from uuid import UUID

    async with db.session_factory() as session:
        row = await session.get(LearningRequestRecord, UUID(request["id"]))
        assert row.status == "superseded"
        assert (await session.get(SkillVersionRecord, version.id)).definition == original


async def selected_method(db, run_id, *, count=1):
    from types import SimpleNamespace

    from evoagent.db.models import ArtifactRecord, RunSkillSelectionRecord, SkillSourceRecord
    from evoagent.skills.canonical import content_hash

    definition = await PersonalDemoGenerator().generate((SimpleNamespace(payload={}),))
    versions = []
    async with db.session_factory() as session:
        for index in range(count):
            name = "original_method" + str(index)
            body = definition.model_copy(update={"name": name}).model_dump(mode="json")
            skill = SkillRecord(name=name, slug=name, description="original", next_version_number=2)
            session.add(skill)
            await session.flush()
            version = SkillVersionRecord(
                skill_id=skill.id,
                version=1,
                schema_version=2,
                definition=body,
                content_hash=content_hash(body),
                extraction_key=content_hash({"seed": name}),
                lifecycle_status=SkillVersionStatus.ACTIVE,
            )
            session.add(version)
            await session.flush()
            artifact = ArtifactRecord(
                run_id=run_id,
                type="learning_source",
                uri="test://immutable",
                content_hash=content_hash({"seed_source": name}),
                size_bytes=0,
                attributes={},
            )
            session.add(artifact)
            await session.flush()
            source = LearningSourceRecord(
                run_id=run_id,
                source_revision=content_hash({"source": name}),
                source_role="personal",
                artifact_id=artifact.id,
                content_hash=artifact.content_hash,
                evidence_manifest={},
                parent_skill_versions=[],
            )
            session.add(source)
            await session.flush()
            session.add(
                SkillSourceRecord(
                    skill_version_id=version.id,
                    source_run_id=run_id,
                    source_kind="personal",
                    learning_source_id=source.id,
                    trace_artifact_id=artifact.id,
                    source_trace_hash=artifact.content_hash,
                )
            )
            skill.active_version_id = version.id
            session.add(
                RunSkillSelectionRecord(
                    run_id=run_id,
                    skill_version_id=version.id,
                    mode="auto",
                    rank=index + 1,
                    score=1,
                    query_terms=[],
                )
            )
            versions.append((skill.id, version.id))
        await session.commit()
    return versions


async def test_method_correction_revises_selected_parent_without_activation(learning_api):
    client, db, run_id, settings = learning_api
    await enable(client)
    skill_id, parent_id = (await selected_method(db, run_id))[0]
    response = await client.post(
        f"/api/v1/runs/{run_id}/feedback",
        json={
            "client_request_id": "correct-method",
            "intent": "method",
            "verdict": "needs_fix",
            "correction": "保留标识符前导零，按文本比较",
            "learn_from_feedback": True,
        },
    )
    assert response.status_code == 201, response.text
    request_id = response.json()["learning_request_id"]
    runner = worker(db, settings)
    for _ in range(3):
        assert await runner.run_once()
    state = (await client.get(f"/api/v1/learning-requests/{request_id}")).json()
    assert state["status"] == "ready_for_review"
    from uuid import UUID

    async with db.session_factory() as session:
        candidate = await session.get(SkillVersionRecord, UUID(state["candidate_version_id"]))
        assert candidate.skill_id == skill_id and candidate.parent_version_id == parent_id
        assert candidate.version == 2 and candidate.lifecycle_status is SkillVersionStatus.DRAFT
        assert "前导零" in candidate.definition["steps"][0]["instruction"]
        assert (await session.get(SkillRecord, skill_id)).active_version_id == parent_id


async def test_ambiguous_selected_methods_save_feedback_and_request_clarification(learning_api):
    client, db, run_id, _ = learning_api
    await enable(client)
    await selected_method(db, run_id, count=2)
    response = await client.post(
        f"/api/v1/runs/{run_id}/feedback",
        json={
            "client_request_id": "ambiguous",
            "intent": "method",
            "verdict": "incorrect",
            "correction": "需要明确修订对象",
            "learn_from_feedback": True,
        },
    )
    assert response.status_code == 201
    assert response.json()["routing"] == "clarify"
    assert response.json()["learning_request_id"] is None
    async with db.session_factory() as session:
        assert not list(await session.scalars(select(LearningRequestRecord)))


async def test_revoked_parent_is_not_reinjected_or_silently_used_for_revision(learning_api):
    client, db, run_id, settings = learning_api
    await enable(client)
    _, parent_id = (await selected_method(db, run_id))[0]
    response = await client.post(
        f"/api/v1/runs/{run_id}/feedback",
        json={
            "client_request_id": "withdrawn-parent",
            "intent": "method",
            "verdict": "needs_fix",
            "correction": "Keep identifiers as text",
            "learn_from_feedback": True,
        },
    )
    assert response.status_code == 201, response.text
    runner = worker(db, settings)
    assert await runner.run_once()  # freeze while the parent is still authorized
    from evoagent.db.models import SkillSourceRecord

    async with db.session_factory() as session:
        link = await session.scalar(
            select(SkillSourceRecord).where(SkillSourceRecord.skill_version_id == parent_id)
        )
        source = await session.get(LearningSourceRecord, link.learning_source_id)
        source.status, source.revocation_epoch = "revoked", source.revocation_epoch + 1
        await session.commit()
    assert await runner.run_once()
    state = (
        await client.get(f"/api/v1/learning-requests/{response.json()['learning_request_id']}")
    ).json()
    assert state["status"] == "failed"
    assert state["error_code"] in {
        "source_selected_skill_unavailable",
        "revision_source_unavailable",
    }
    async with db.session_factory() as session:
        assert len(list(await session.scalars(select(SkillVersionRecord)))) == 1


async def test_expired_generator_cannot_commit_after_another_owner_takes_lease(learning_api):
    client, db, run_id, settings = learning_api
    request = await submit(client, run_id)
    entered, cancelled = asyncio.Event(), asyncio.Event()

    class BlockingGenerator:
        async def generate(self, sources, *, context=None):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

    runner = worker(db, settings, BlockingGenerator())
    assert await runner.run_once()
    work = asyncio.create_task(runner.run_once())
    await asyncio.wait_for(entered.wait(), 5)
    from evoagent.db.models import MaintenanceJobRecord

    async with db.session_factory() as session:
        job = await session.scalar(
            select(MaintenanceJobRecord).where(MaintenanceJobRecord.status == "running")
        )
        job.lease_owner, job.lease_epoch = "new-owner", job.lease_epoch + 1
        await session.commit()
    await asyncio.wait_for(work, 5)
    assert cancelled.is_set()
    state = (await client.get(f"/api/v1/learning-requests/{request['id']}")).json()
    assert state["candidate_version_id"] is None
    assert state["status"] in {"queued", "running"}
    async with db.session_factory() as session:
        assert not list(await session.scalars(select(SkillVersionRecord)))


async def test_production_learning_assembly_runs_offline_without_eval_or_spend(learning_api):
    from evoagent.db.models import EvalRunRecord, SpendRecord
    from evoagent.learning.bootstrap import assemble_learning

    client, db, run_id, settings = learning_api
    request = await submit(client, run_id)
    store = LocalArtifactStore(settings.artifact_root)
    handler, provider = assemble_learning(db.session_factory, store, settings, None)
    assert provider is None
    runner = MaintenanceWorker(
        db.session_factory,
        store,
        handlers={"learning_propose": handler, "learning_validate": handler},
        allowed_kinds={"learning_propose", "learning_validate"},
    )
    for _ in range(3):
        assert await runner.run_once()
    state = (await client.get(f"/api/v1/learning-requests/{request['id']}")).json()
    assert state["status"] == "ready_for_review", state
    async with db.session_factory() as session:
        assert not list(await session.scalars(select(EvalRunRecord)))
        assert not list(await session.scalars(select(SpendRecord)))


async def test_worker_configuration_change_requires_a_new_request(learning_api):
    from evoagent.learning.bootstrap import assemble_learning

    client, db, run_id, settings = learning_api
    request = await submit(client, run_id)
    store = LocalArtifactStore(settings.artifact_root)
    changed = settings.model_copy(update={"skill_max_steps": settings.skill_max_steps + 1})
    handler, _ = assemble_learning(db.session_factory, store, changed, None)
    runner = MaintenanceWorker(
        db.session_factory,
        store,
        handlers={"learning_propose": handler, "learning_validate": handler},
        allowed_kinds={"learning_propose", "learning_validate"},
    )
    assert await runner.run_once()
    assert await runner.run_once()
    state = (await client.get(f"/api/v1/learning-requests/{request['id']}")).json()
    assert state["status"] == "failed"
    assert state["error_code"] == "learning_configuration_changed"


async def test_cleanup_lane_is_not_blocked_by_candidate_generation(learning_api):
    import time
    from uuid import UUID

    from evoagent.db.models import ArtifactRecord

    client, db, run_id, settings = learning_api
    request = await submit(client, run_id)
    entered = asyncio.Event()

    class WaitingGenerator:
        async def generate(self, sources, *, context=None):
            entered.set()
            await asyncio.Event().wait()

    normal = worker(db, settings, WaitingGenerator())
    assert await normal.run_once()
    normal_work = asyncio.create_task(normal.run_once())
    await asyncio.wait_for(entered.wait(), 5)
    state = (await client.get(f"/api/v1/learning-requests/{request['id']}")).json()
    started = time.monotonic()
    response = await client.post(
        f"/api/v1/learning-sources/{state['source']['id']}/revoke",
        json={"reason": "withdraw", "expected_status": "valid"},
    )
    assert response.status_code == 200, response.text
    critical = worker(db, settings)
    critical.lane = "critical"
    assert await asyncio.wait_for(critical.run_once(), 2)
    assert time.monotonic() - started <= 2
    await asyncio.wait_for(normal_work, 5)
    async with db.session_factory() as session:
        source = await session.get(LearningSourceRecord, UUID(state["source"]["id"]))
        artifact = await session.get(ArtifactRecord, source.artifact_id)
        assert artifact.attributes["erased"]
        assert not list(await session.scalars(select(SkillVersionRecord)))


async def test_unchanged_definition_is_skipped_without_a_second_version(learning_api):
    from evoagent.db.models import DEFAULT_WORKSPACE_ID

    client, db, run_id, settings = learning_api
    first = await submit(client, run_id)
    runner = worker(db, settings)
    for _ in range(3):
        assert await runner.run_once()
    changed = await client.put(
        f"/api/v1/workspaces/{DEFAULT_WORKSPACE_ID}/learning-policy",
        json={"mode": "manual", "expected_lock_version": 1, "cooldown_seconds": 60},
    )
    assert changed.status_code == 200, changed.text
    response = await client.post(
        f"/api/v1/runs/{run_id}/learning-requests", json={"client_request_id": "duplicate"}
    )
    assert response.status_code == 202, response.text
    assert response.json()["id"] != first["id"]
    for _ in range(2):
        assert await runner.run_once()
    state = (await client.get(f"/api/v1/learning-requests/{response.json()['id']}")).json()
    assert state["status"] == "skipped"
    async with db.session_factory() as session:
        assert len(list(await session.scalars(select(SkillVersionRecord)))) == 1
