from uuid import uuid4

import pytest

from evoagent.db.base import Base
from evoagent.db.models import (
    ArtifactRecord,
    RunEventRecord,
    RunRecord,
    SessionRecord,
    TaskRecord,
    ToolCallRecord,
    ToolEffectRecord,
    TurnRecord,
)
from evoagent.db.session import Database
from evoagent.learning.repository import LearningRepository
from evoagent.learning.schema import FeedbackPayload, LearningError
from evoagent.learning.sources import PersonalSourceService


@pytest.fixture
async def source_db(tmp_path):
    db = Database(f"sqlite+aiosqlite:///{tmp_path / 'sources.db'}")
    async with db.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with db.session_factory() as session:
        chat = SessionRecord(title="personal")
        session.add(chat)
        await session.flush()
        task = TaskRecord(session_id=chat.id, goal="retain leading zeros", status="completed")
        session.add(task)
        await session.flush()
        run = RunRecord(
            task_id=task.id,
            provider="mock",
            model="mock",
            data_role="personal",
            status="completed",
            final_answer="All rows verified (a model assertion)",
        )
        session.add(run)
        await session.commit()
    try:
        yield db, run.id
    finally:
        await db.dispose()


async def feedback(db, run_id, *, intent="method", consent=True, verdict="helpful"):
    async with db.session_factory() as session:
        row = await LearningRepository(session).append_feedback(
            run_id,
            str(uuid4()),
            FeedbackPayload(
                intent=intent,
                verdict=verdict,
                learn_from_feedback=consent,
                correction="use strings",
            ),
            "local-user",
        )
        await session.commit()
        return row


@pytest.mark.parametrize("intent,consent", [("method", False), ("fact", True), ("unsure", True)])
async def test_feedback_intent_does_not_imply_method_consent(source_db, intent, consent):
    db, run_id = source_db
    row = await feedback(db, run_id, intent=intent, consent=consent)
    with pytest.raises(LearningError, match="source_method_consent_required"):
        await PersonalSourceService(db.session_factory).check(run_id, row.id, "feedback")


async def test_completed_is_not_business_success_and_artifact_paths_are_not_read(source_db):
    db, run_id = source_db
    row = await feedback(db, run_id, verdict="needs_fix")
    async with db.session_factory() as session:
        session.add(
            ArtifactRecord(
                run_id=run_id,
                type="tool_output",
                uri="missing/outside/path",
                content_hash="sha256:" + "a" * 64,
                size_bytes=50,
                attributes={},
            )
        )
        session.add(
            RunEventRecord(
                run_id=run_id,
                sequence=1,
                event_type="model.delta",
                payload={"text": "tests passed"},
            )
        )
        await session.commit()
    evidence = await PersonalSourceService(db.session_factory).build_evidence(run_id, row.id)
    assert not evidence.outcome["user_reported_helpful"]
    assert not evidence.verified_facts
    assert evidence.outcome["final_answer_origin"] == "model_inference"
    assert evidence.unknowns == ("completion_is_not_business_success",)
    assert not evidence.artifact_refs[0]["body_read"]


async def test_unknown_effect_is_excluded_even_when_user_calls_result_helpful(source_db):
    db, run_id = source_db
    row = await feedback(db, run_id)
    async with db.session_factory() as session:
        turn = TurnRecord(run_id=run_id, sequence=1, status="completed")
        session.add(turn)
        await session.flush()
        call = ToolCallRecord(
            run_id=run_id,
            turn_id=turn.id,
            provider_call_id="write",
            tool_name="edit_file",
            arguments={},
            risk="R1",
            status="succeeded",
        )
        session.add(call)
        await session.flush()
        session.add(
            ToolEffectRecord(
                tool_call_id=call.id, effect_scope="test", semantic_key="write", status="unknown"
            )
        )
        await session.commit()
    with pytest.raises(LearningError, match="source_effect_unresolved"):
        await PersonalSourceService(db.session_factory).check(run_id, row.id, "feedback")


async def test_wrong_run_feedback_and_in_progress_run_are_rejected(source_db):
    db, run_id = source_db
    source = PersonalSourceService(db.session_factory)
    with pytest.raises(LearningError, match="source_feedback_mismatch"):
        await source.check(run_id, uuid4(), "manual")
    async with db.session_factory() as session:
        run = await session.get(RunRecord, run_id)
        run.status = "running"
        await session.commit()
    with pytest.raises(LearningError, match="source_run_not_terminal"):
        await source.check(run_id, None, "manual")


@pytest.mark.parametrize("role", ["dev", "train", "holdout", "runtime_eval", "legacy"])
async def test_nonpersonal_runs_never_become_personal_learning_sources(source_db, role):
    db, run_id = source_db
    async with db.session_factory() as session:
        original = await session.get(RunRecord, run_id)
        other = RunRecord(
            task_id=original.task_id,
            provider="mock",
            model="mock",
            status="completed",
            data_role=role,
        )
        session.add(other)
        await session.commit()
        forbidden_id = other.id
    with pytest.raises(LearningError, match="personal_source_role_required"):
        await PersonalSourceService(db.session_factory).check(forbidden_id, None, "manual")


async def test_evidence_rechecks_secrets_without_changing_user_task(source_db):
    db, run_id = source_db
    async with db.session_factory() as session:
        run = await session.get(RunRecord, run_id)
        task = await session.get(TaskRecord, run.task_id)
        task.goal = "password: hidden-value"
        await session.commit()
        task_id = task.id
    evidence = await PersonalSourceService(db.session_factory).build_evidence(run_id, None)
    assert "hidden-value" not in evidence.model_dump_json()
    assert evidence.redacted and evidence.redaction_policy_version > 0
    async with db.session_factory() as session:
        assert (await session.get(TaskRecord, task_id)).goal == "password: hidden-value"
