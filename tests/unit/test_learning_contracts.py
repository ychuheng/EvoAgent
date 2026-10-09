import asyncio
import os
from uuid import uuid4

import pytest
from sqlalchemy import select, update
from sqlalchemy.exc import DBAPIError

from evoagent.db.base import Base
from evoagent.db.counters import allocate
from evoagent.db.models import (
    DEFAULT_WORKSPACE_ID,
    LearningRequestAliasRecord,
    RunRecord,
    SessionRecord,
    TaskRecord,
)
from evoagent.db.repositories.base import ConcurrentUpdateError
from evoagent.db.session import Database
from evoagent.learning.planner import route_feedback
from evoagent.learning.repository import LearningRepository
from evoagent.learning.schema import FeedbackPayload
from evoagent.skills.canonical import content_hash


@pytest.fixture(params=["sqlite", "postgres"])
async def learning_db(tmp_path, request):
    url = f"sqlite+aiosqlite:///{tmp_path / 'learning.db'}"
    if request.param == "postgres":
        url = os.getenv("EVOAGENT_TEST_DATABASE_URL")
        if not url:
            pytest.skip("PostgreSQL learning contracts require an isolated test database")
    database = Database(url)
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with database.session_factory() as session:
        chat = SessionRecord(title="personal")
        session.add(chat)
        await session.flush()
        task = TaskRecord(session_id=chat.id, goal="test")
        session.add(task)
        await session.flush()
        run = RunRecord(task_id=task.id, provider="mock", model="mock", data_role="personal")
        session.add(run)
        await session.commit()
        run_id = run.id
    yield database, run_id
    await database.dispose()


async def append(database, run_id, key, **values):
    async with database.session_factory() as session:
        record = await LearningRepository(session).append_feedback(
            run_id, key, FeedbackPayload(intent="method", verdict="helpful", **values), "human"
        )
        await session.commit()
        return record


async def test_feedback_semantics_revisions_replay_and_conflict(learning_db):
    db, run_id = learning_db
    a = await append(db, run_id, "a", comment="first")
    b = await append(db, run_id, "b", comment="new comment")
    c = await append(db, run_id, "c", correction="new method")
    d = await append(db, run_id, "d", correction="")
    assert [r.revision for r in (a, b, c, d)] == [1, 2, 3, 4]
    assert [r.learning_revision for r in (a, b, c, d)] == [1, 1, 2, 3]
    assert a.learning_payload_hash == b.learning_payload_hash == d.learning_payload_hash
    assert b.supersedes_id == a.id
    async with db.session_factory() as session:
        repo = LearningRepository(session)
        replay = await repo.append_feedback(
            run_id,
            "a",
            FeedbackPayload(intent="method", verdict="helpful", comment="first"),
            "human",
            expected_revision=0,
        )
        assert replay.id == a.id
        with pytest.raises(ConcurrentUpdateError, match="feedback_conflict"):
            await repo.append_feedback(
                run_id, "a", FeedbackPayload(intent="method", verdict="incorrect"), "human"
            )
    assert (await append(db, run_id, "e")).revision == 5


async def test_concurrent_feedback_and_counter_rollback(learning_db):
    db, run_id = learning_db
    records = await asyncio.gather(*(append(db, run_id, str(i)) for i in range(8)))
    assert sorted(r.revision for r in records) == list(range(1, 9))
    async with db.session_factory() as session:
        assert await allocate(session, RunRecord, run_id, "next_feedback_revision") == 9
        await session.rollback()
    assert (await append(db, run_id, "after-rollback")).revision == 9


async def test_run_role_cannot_be_reclassified_by_orm_or_bulk_sql(learning_db):
    db, run_id = learning_db
    async with db.session_factory() as session:
        run = await session.get(RunRecord, run_id)
        run.data_role = "train"
        with pytest.raises(ValueError, match="immutable"):
            await session.flush()
        await session.rollback()
    async with db.session_factory() as session:
        with pytest.raises(DBAPIError, match="data_role is immutable"):
            await session.execute(
                update(RunRecord).where(RunRecord.id == run_id).values(data_role="holdout")
            )
        await session.rollback()


async def test_feedback_evidence_order_is_not_a_semantic_change(learning_db):
    db, run_id = learning_db
    refs = [{"artifact_id": "one"}, {"artifact_id": "two"}]
    a = await append(db, run_id, "a", evidence_refs=refs)
    b = await append(db, run_id, "b", evidence_refs=[refs[1], refs[0], refs[0]])
    assert b.learning_revision == a.learning_revision == 1
    assert b.learning_payload_hash == a.learning_payload_hash
    async with db.session_factory() as session:
        record = await session.get(type(a), a.id)
        record.comment = "overwrite history"
        with pytest.raises(ValueError, match="append-only"):
            await session.flush()


async def test_all_semantic_aliases_remain_idempotent(learning_db):
    db, run_id = learning_db
    policy = {"mode": "manual"}
    frozen = dict(
        run_id=str(run_id),
        learning_revision=0,
        learning_payload_hash="",
        source_revision=content_hash("source"),
        target_skill_id=None,
        base_version_id=None,
        policy_hash=content_hash(policy),
    )
    async with db.session_factory() as session:
        repo = LearningRepository(session)
        arguments = dict(
            workspace_id=DEFAULT_WORKSPACE_ID,
            origin_run_id=run_id,
            kind="propose",
            frozen_inputs=frozen,
            policy_snapshot=policy,
        )
        a = await repo.append_request(**arguments, client_request_id="first", request_body={})
        b = await repo.append_request(**arguments, client_request_id="second", request_body={})
        assert a.id == b.id
        await session.commit()
    async with db.session_factory() as session:
        assert len(tuple(await session.scalars(select(LearningRequestAliasRecord)))) == 2
        with pytest.raises(ConcurrentUpdateError, match="learning_request_conflict"):
            await LearningRepository(session).append_request(
                **arguments, client_request_id="second", request_body={"changed": True}
            )


def test_namespaces_and_router_are_explicit():
    with pytest.raises(ValueError, match="missing frozen identity"):
        LearningRepository.build_source_key("validate", {})
    validate = {
        key: str(uuid4())
        for key in (
            "parent_request_id",
            "candidate_version_id",
            "candidate_content_hash",
            "validation_input_manifest_hash",
            "validation_criteria_hash",
            "validation_policy_hash",
            "validator_version",
            "target_scope_key",
        )
    }
    assert LearningRepository.build_source_key("validate", validate).startswith("validate:v1:")
    for intent, expected in [
        ("method", "method"),
        ("fact", "fact"),
        ("mixed", "clarify"),
        ("unsure", "clarify"),
    ]:
        payload = FeedbackPayload(intent=intent, verdict="helpful", learn_from_feedback=True)
        assert route_feedback(payload) == expected
        assert route_feedback(payload.model_copy(update={"learn_from_feedback": False})) == "none"


def test_feedback_construction_has_one_production_entry():
    import ast
    from pathlib import Path

    constructors = []
    for path in Path("src").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "RunFeedbackRecord"
            ):
                constructors.append(path.as_posix())
    assert constructors == ["src/evoagent/learning/repository.py"]


def test_criteria_require_machine_evidence_and_identified_human():
    from evoagent.learning.schema import CriterionResult, LearningCriterion, criteria_passed

    criteria = (
        LearningCriterion(
            criterion_id="tests", source="machine", expected=0, required_evidence=("tool-result",)
        ),
        LearningCriterion(criterion_id="review", source="user", expected=True),
    )
    machine = CriterionResult(
        criterion_id="tests", source="machine", status="passed", evidence_refs=("tool-result",)
    )
    user = CriterionResult(criterion_id="review", source="user", status="pending")
    assert not criteria_passed(criteria, (machine, user))
    user = user.model_copy(update={"status": "passed"})
    assert not criteria_passed(criteria, (machine, user))
    user = user.model_copy(update={"actor_id": "human"})
    assert criteria_passed(criteria, (machine, user))
    assert not criteria_passed(criteria, (machine.model_copy(update={"evidence_refs": ()}), user))


async def test_sensitive_feedback_is_rejected_before_counter_allocation(learning_db):
    db, run_id = learning_db
    with pytest.raises(ValueError, match="sensitive_feedback_content"):
        await append(db, run_id, "secret", comment="password: hidden-value")
    assert (await append(db, run_id, "safe")).revision == 1
