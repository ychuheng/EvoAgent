import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import create_async_engine

from evoagent.api.app import create_app
from evoagent.config import Settings
from evoagent.db.base import Base
from evoagent.db.models import (
    DEFAULT_WORKSPACE_ID,
    LearningRequestAliasRecord,
    LearningRequestRecord,
    MaintenanceJobRecord,
    RunFeedbackRecord,
    RunRecord,
    SessionRecord,
    TaskRecord,
)
from evoagent.db.session import Database
from evoagent.learning.jobs import LearningJobGuard
from evoagent.learning.schema import LearningError
from evoagent.learning.sources import PersonalSourceService
from evoagent.trace.artifacts import LocalArtifactStore


@pytest.fixture(params=["sqlite", "postgres"])
async def learning_api(tmp_path, request):
    url = f"sqlite+aiosqlite:///{tmp_path / 'learning-api.db'}"
    schema = None
    if request.param == "postgres":
        url = os.getenv("EVOAGENT_TEST_DATABASE_URL")
        if not url:
            pytest.skip("requires isolated PostgreSQL")
        schema = "learning_api_" + uuid4().hex
    db = Database(url)
    if schema is not None:
        async with db.engine.begin() as connection:
            await connection.execute(
                text("CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public")
            )
            await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        await db.dispose()
        db.engine = create_async_engine(
            url,
            connect_args={"server_settings": {"search_path": f"{schema},public"}},
            execution_options={"schema_translate_map": {None: schema}},
        )
        db.session_factory.configure(bind=db.engine)
    async with db.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with db.session_factory() as session:
        chat = SessionRecord(title="personal")
        session.add(chat)
        await session.flush()
        task = TaskRecord(session_id=chat.id, goal="preserve leading zeros", status="completed")
        session.add(task)
        await session.flush()
        run = RunRecord(
            task_id=task.id, provider="mock", model="mock", status="completed", data_role="personal"
        )
        session.add(run)
        await session.commit()
    settings = Settings(
        _env_file=None,
        workspace=tmp_path,
        artifact_root=tmp_path / "artifacts",
        learning_enabled=True,
    )
    app = create_app(settings, database=db)
    try:
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client,
        ):
            yield client, db, run.id, settings
    finally:
        if schema is not None:
            async with db.engine.begin() as connection:
                await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await db.dispose()


async def enable(client):
    response = await client.put(
        f"/api/v1/workspaces/{DEFAULT_WORKSPACE_ID}/learning-policy",
        json={"mode": "manual", "expected_lock_version": 0},
    )
    assert response.status_code == 200, response.text


async def count(db, record):
    async with db.session_factory() as session:
        return await session.scalar(select(func.count()).select_from(record))


async def test_feedback_and_job_are_atomic_and_replay_does_not_duplicate(learning_api):
    client, db, run_id, _ = learning_api
    await enable(client)
    body = {
        "intent": "method",
        "verdict": "needs_fix",
        "correction": "read identifiers as strings",
        "learn_from_feedback": True,
        "client_request_id": "feedback-1",
    }
    first = await client.post(f"/api/v1/runs/{run_id}/feedback", json=body)
    assert first.status_code == 201, first.text
    repeated = await client.post(f"/api/v1/runs/{run_id}/feedback", json=body)
    assert repeated.json() == first.json()
    assert await count(db, RunFeedbackRecord) == 1
    assert await count(db, LearningRequestRecord) == 1
    assert await count(db, MaintenanceJobRecord) == 1
    comment = {**body, "client_request_id": "feedback-2", "comment": "different UI note"}
    second = await client.post(f"/api/v1/runs/{run_id}/feedback", json=comment)
    assert second.status_code == 201, second.text
    assert second.json()["learning_request_id"] == first.json()["learning_request_id"]
    assert second.json()["learning_revision"] == first.json()["learning_revision"]
    assert await count(db, MaintenanceJobRecord) == 1
    assert await count(db, LearningRequestAliasRecord) == 2
    conflict = await client.post(
        f"/api/v1/runs/{run_id}/feedback", json={**body, "correction": "different"}
    )
    assert conflict.status_code == 409


async def test_policy_off_rolls_back_feedback_and_revision(learning_api):
    client, db, run_id, _ = learning_api
    response = await client.post(
        f"/api/v1/runs/{run_id}/feedback",
        json={
            "intent": "method",
            "verdict": "helpful",
            "learn_from_feedback": True,
            "client_request_id": "off",
        },
    )
    assert response.status_code == 409
    assert await count(db, RunFeedbackRecord) == await count(db, MaintenanceJobRecord) == 0
    async with db.session_factory() as session:
        assert (await session.get(RunRecord, run_id)).next_feedback_revision == 1


@pytest.mark.parametrize(
    "intent,route", [("fact", "fact"), ("unsure", "clarify"), ("mixed", "clarify")]
)
async def test_fact_and_uncertain_feedback_do_not_create_method_jobs(learning_api, intent, route):
    client, db, run_id, _ = learning_api
    response = await client.post(
        f"/api/v1/runs/{run_id}/feedback",
        json={
            "intent": intent,
            "verdict": "helpful",
            "learn_from_feedback": True,
            "client_request_id": "route",
        },
    )
    assert response.status_code == 201, response.text
    assert response.json()["routing"] == route
    assert await count(db, LearningRequestRecord) == await count(db, MaintenanceJobRecord) == 0


async def test_learning_disabled_is_not_half_success(learning_api):
    client, db, run_id, settings = learning_api
    settings.learning_enabled = False
    response = await client.post(
        f"/api/v1/runs/{run_id}/feedback",
        json={
            "intent": "method",
            "verdict": "helpful",
            "learn_from_feedback": True,
            "client_request_id": "disabled",
        },
    )
    assert response.status_code == 409
    assert await count(db, RunFeedbackRecord) == 0
    response = await client.post(
        f"/api/v1/runs/{run_id}/feedback",
        json={
            "intent": "method",
            "verdict": "helpful",
            "client_request_id": "plain",
        },
    )
    assert response.status_code == 201 and response.json()["routing"] == "none"


async def test_manual_request_location_pagination_cancel_and_stale_guard(learning_api):
    client, db, run_id, _ = learning_api
    await enable(client)
    response = await client.post(
        f"/api/v1/runs/{run_id}/learning-requests", json={"client_request_id": "manual"}
    )
    assert response.status_code == 202, response.text
    location = response.headers["Location"]
    view = response.json()
    detail = (await client.get(location)).json()
    assert detail.pop("cost") == {
        "known_spent_micros": 0,
        "outstanding_reserved_micros": 0,
        "unknown_usage_count": 0,
    }
    assert detail == {key: value for key, value in view.items() if key != "cost"}
    listed = await client.get(
        "/api/v1/learning-requests", params={"workspace_id": str(DEFAULT_WORKSPACE_ID), "limit": 1}
    )
    assert len(listed.json()["items"]) == 1 and listed.json()["next_cursor"] is None
    cancelled = await client.post(location + "/cancel", json={"expected_lock_version": 0})
    assert cancelled.status_code == 200 and cancelled.json()["status"] == "cancelled"
    async with db.session_factory() as session:
        job = await session.scalar(select(MaintenanceJobRecord))
        assert job.cancel_requested and job.status == "cancelled"
    assert (
        await client.post(location + "/cancel", json={"expected_lock_version": 0})
    ).status_code == 409
    replay = await client.post(
        f"/api/v1/runs/{run_id}/learning-requests", json={"client_request_id": "manual"}
    )
    assert replay.status_code == 202 and replay.json()["id"] == view["id"]
    assert await count(db, MaintenanceJobRecord) == 1


async def test_secret_feedback_rejected_without_echo_or_revision(learning_api):
    client, db, run_id, _ = learning_api
    response = await client.post(
        f"/api/v1/runs/{run_id}/feedback",
        json={
            "intent": "method",
            "verdict": "helpful",
            "comment": "password: hidden-value",
            "client_request_id": "secret",
        },
    )
    assert response.status_code == 422 and "hidden-value" not in response.text
    assert await count(db, RunFeedbackRecord) == 0
    assert (await client.get(f"/api/v1/learning-requests/{uuid4()}")).status_code == 404


async def running_source_job(client, db, run_id):
    await enable(client)
    response = await client.post(
        f"/api/v1/runs/{run_id}/learning-requests", json={"client_request_id": "freeze"}
    )
    assert response.status_code == 202, response.text
    async with db.session_factory() as session:
        row = await session.scalar(select(LearningRequestRecord))
        job = await session.scalar(select(MaintenanceJobRecord))
        job.status = "running"
        job.lease_owner = "source-test"
        job.lease_epoch = 1
        job.lease_expires_at = datetime.now(UTC) + timedelta(seconds=60)
        await session.commit()
        return row, LearningJobGuard(job.id, "source-test", 1)


async def test_frozen_source_reads_immutable_body_then_revocation_stops_injection(learning_api):
    client, db, run_id, settings = learning_api
    row, guard = await running_source_job(client, db, run_id)
    sources = PersonalSourceService(
        db.session_factory, artifact_store=LocalArtifactStore(settings.artifact_root)
    )
    frozen = await sources.freeze(
        run_id, None, source_revision=row.frozen_inputs["source_revision"], job_guard=guard
    )
    again = await sources.freeze(
        run_id, None, source_revision=row.frozen_inputs["source_revision"], job_guard=guard
    )
    assert frozen.id == again.id
    async with db.session_factory() as session:
        run = await session.get(RunRecord, run_id)
        task = await session.get(TaskRecord, run.task_id)
        task.goal = "a changed original task"
        await session.commit()
    evidence = await sources.read_frozen(frozen.id)
    assert evidence.goal == "preserve leading zeros"
    response = await client.post(
        f"/api/v1/learning-sources/{frozen.id}/revoke",
        json={
            "reason": "withdraw method permission",
            "expected_status": "valid",
        },
    )
    assert response.status_code == 200, response.text
    revoked = response.json()["source"]
    assert revoked["status"] == "revoked" and revoked["revocation_epoch"] == 1
    assert response.json()["affected_request_ids"] == [str(row.id)]
    assert not response.json()["impact_truncated"]
    with pytest.raises(LearningError, match="learning_source_revoked"):
        await sources.read_frozen(frozen.id)
    async with db.session_factory() as session:
        request = await session.get(LearningRequestRecord, row.id)
        job = await session.get(MaintenanceJobRecord, guard.job_id)
        assert request.status == "superseded" and job.cancel_requested


async def test_revocation_between_source_read_and_return_discards_body(learning_api):
    client, db, run_id, settings = learning_api
    row, guard = await running_source_job(client, db, run_id)
    store = LocalArtifactStore(settings.artifact_root)
    sources = PersonalSourceService(db.session_factory, artifact_store=store)
    frozen = await sources.freeze(
        run_id, None, source_revision=row.frozen_inputs["source_revision"], job_guard=guard
    )

    class RevokeDuringRead:
        async def read_bounded(self, uri, *, max_bytes):
            body = await store.read_bounded(uri, max_bytes=max_bytes)
            await sources.revoke(frozen.id, "revoked during read")
            return body

    racing = PersonalSourceService(db.session_factory, artifact_store=RevokeDuringRead())
    with pytest.raises(LearningError, match="learning_source_revoked"):
        await racing.read_frozen(frozen.id)


async def test_fenced_learning_job_cannot_register_frozen_source(learning_api):
    client, db, run_id, settings = learning_api
    row, guard = await running_source_job(client, db, run_id)
    sources = PersonalSourceService(
        db.session_factory, artifact_store=LocalArtifactStore(settings.artifact_root)
    )
    wrong = LearningJobGuard(guard.job_id, guard.owner, guard.epoch + 1)
    with pytest.raises(LearningError, match="learning_job_fenced"):
        await sources.freeze(
            run_id, None, source_revision=row.frozen_inputs["source_revision"], job_guard=wrong
        )
    assert not any(path.is_file() for path in settings.artifact_root.rglob("*"))


async def test_retry_creates_new_job_and_same_client_id_replays_once(learning_api):
    client, db, run_id, _ = learning_api
    await enable(client)
    response = await client.post(
        f"/api/v1/runs/{run_id}/learning-requests", json={"client_request_id": "first"}
    )
    async with db.session_factory() as session:
        row = await session.scalar(select(LearningRequestRecord))
        job = await session.scalar(select(MaintenanceJobRecord))
        row.status = "failed"
        job.status = "failed"
        await session.commit()
    url = response.headers["Location"] + "/retry"
    body = {"expected_lock_version": 0, "client_request_id": "retry-1"}
    first = await client.post(url, json=body)
    second = await client.post(url, json=body)
    assert first.status_code == second.status_code == 202, first.text
    assert first.json() == second.json()
    assert await count(db, MaintenanceJobRecord) == 2
    changed = await client.post(url, json={**body, "expected_lock_version": 1})
    assert changed.status_code == 409


@pytest.mark.parametrize("path", ["/skills/extract", "/skills/extractions"])
async def test_legacy_extraction_cannot_bypass_disabled_learning(learning_api, path):
    client, db, _, settings = learning_api
    settings.learning_enabled = False
    response = await client.post("/api/v1" + path, json={"source_eval_run_ids": [str(uuid4())]})
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "learning_disabled"
    assert await count(db, LearningRequestRecord) == 0


async def test_acknowledging_candidate_never_activates_formal_version(learning_api):
    from evoagent.db.models import SkillRecord, SkillVersionRecord
    from evoagent.skills.canonical import content_hash
    from tests.unit.test_phase_three_services import make_skill

    client, db, run_id, settings = learning_api
    row, guard = await running_source_job(client, db, run_id)
    sources = PersonalSourceService(
        db.session_factory, artifact_store=LocalArtifactStore(settings.artifact_root)
    )
    await sources.freeze(
        run_id, None, source_revision=row.frozen_inputs["source_revision"], job_guard=guard
    )
    async with db.session_factory() as session:
        skill = SkillRecord(name="numbers", slug="numbers", description="read numbers safely")
        session.add(skill)
        await session.flush()
        definition = make_skill("numbers", "read numbers safely", ("numbers",)).model_dump(
            mode="json"
        )
        candidate = SkillVersionRecord(
            skill_id=skill.id,
            version=1,
            schema_version=1,
            definition=definition,
            content_hash=content_hash(definition),
            extraction_key=content_hash({"candidate": str(row.id)}),
        )
        session.add(candidate)
        await session.flush()
        request = await session.get(LearningRequestRecord, row.id)
        request.candidate_version_id = candidate.id
        request.status = "ready_for_review"
        request.validation_report = {
            "static_validation": {"passed": True},
            "validation_mode": "static_only",
        }
        request.validation_report_hash = content_hash(request.validation_report)
        await session.commit()
        skill_id, candidate_id = skill.id, candidate.id
    response = await client.post(
        f"/api/v1/learning-requests/{row.id}/review",
        json={
            "expected_lock_version": 0,
            "action": "acknowledge",
            "reason": "method is worth testing separately",
        },
    )
    assert response.status_code == 200 and response.json()["status"] == "completed", response.text
    async with db.session_factory() as session:
        skill = await session.get(SkillRecord, skill_id)
        candidate = await session.get(SkillVersionRecord, candidate_id)
        assert skill.active_version_id is None and str(candidate.lifecycle_status) == "draft"


@pytest.mark.parametrize("status,actual", [("unknown", None), ("settled", None), ("settled", -1)])
async def test_unknown_or_malformed_usage_stays_reserved_and_blocks_retry(
    learning_api, status, actual
):
    from evoagent.db.models import LearningSpendReservationRecord

    client, db, run_id, _ = learning_api
    await enable(client)
    response = await client.post(
        f"/api/v1/runs/{run_id}/learning-requests", json={"client_request_id": "usage"}
    )
    async with db.session_factory() as session:
        row = await session.scalar(select(LearningRequestRecord))
        row.status = "failed"
        session.add(
            LearningSpendReservationRecord(
                workspace_id=DEFAULT_WORKSPACE_ID,
                request_id=row.id,
                call_key=str(uuid4()),
                budget_day="2026-10-09",
                reserved_micros=1000,
                status=status,
                actual_micros=actual,
            )
        )
        await session.commit()
    detail = await client.get(response.headers["Location"])
    assert detail.json()["cost"] == {
        "known_spent_micros": 0,
        "outstanding_reserved_micros": 1000,
        "unknown_usage_count": 1,
    }
    retry = await client.post(
        response.headers["Location"] + "/retry",
        json={"expected_lock_version": 0, "client_request_id": "retry"},
    )
    assert (
        retry.status_code == 409 and retry.json()["detail"]["code"] == "learning_usage_unresolved"
    )
    assert await count(db, MaintenanceJobRecord) == 1


async def test_sensitive_frozen_source_stays_quarantined_after_later_scan_changes(
    learning_api, monkeypatch
):
    from evoagent.db.models import ArtifactRecord
    from evoagent.privacy.artifact_access import ArtifactInjectionGuard, ArtifactSensitiveContent

    client, db, run_id, settings = learning_api
    row, guard = await running_source_job(client, db, run_id)
    sources = PersonalSourceService(
        db.session_factory, artifact_store=LocalArtifactStore(settings.artifact_root)
    )
    frozen = await sources.freeze(
        run_id, None, source_revision=row.frozen_inputs["source_revision"], job_guard=guard
    )
    original = ArtifactInjectionGuard.verify_derived_text

    async def sensitive(self, **kwargs):
        raise ArtifactSensitiveContent("current policy blocked a frozen source")

    monkeypatch.setattr(ArtifactInjectionGuard, "verify_derived_text", sensitive)
    with pytest.raises(ArtifactSensitiveContent):
        await sources.read_frozen(frozen.id)
    async with db.session_factory() as session:
        assert (
            await session.get(ArtifactRecord, frozen.artifact_id)
        ).redaction_status == "quarantined"
    monkeypatch.setattr(ArtifactInjectionGuard, "verify_derived_text", original)
    with pytest.raises(LearningError, match="learning_source_quarantined"):
        await sources.read_frozen(frozen.id)
