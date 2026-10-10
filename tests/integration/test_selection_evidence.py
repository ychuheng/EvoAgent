"""Database evidence contracts; synthetic bindings do not prove adoption."""

import pytest
import test_lease_fencing as fencing
from sqlalchemy.exc import IntegrityError

from evoagent.db.models import RunSkillSelectionRecord
from tests.integration.test_skill_applicability_runtime import setup

fencing_db = fencing.fencing_db
HASH = "sha256:" + "a" * 64


def binding(run_id, version_id, **overrides):
    fields = dict(
        run_id=run_id,
        skill_version_id=version_id,
        origin="formal",
        mode="retrieval",
        rank=1,
        score=1,
        query_terms=[],
        scope_key="fixture-scope",
        content_hash=HASH,
        rendered_hash=HASH,
        applicability={"status": "applicable"},
        selection_policy_version="skill-selector-v1",
    )
    fields.update(overrides)
    return RunSkillSelectionRecord(**fields)


@pytest.mark.parametrize(
    "missing",
    ["content_hash", "rendered_hash", "scope_key", "applicability", "selection_policy_version"],
)
async def test_incomplete_v3_evidence_is_rejected(fencing_db, missing):
    aggregate, identity = await setup(fencing_db, "data")
    async with fencing_db.session_factory() as session:
        session.add(binding(aggregate.run.id, identity, **{missing: None}))
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()


async def test_only_one_v3_selection_per_run(fencing_db):
    aggregate, identity = await setup(fencing_db, "data")
    async with fencing_db.session_factory() as session:
        session.add(binding(aggregate.run.id, identity))
        await session.commit()
    async with fencing_db.session_factory() as session:
        session.add(binding(aggregate.run.id, identity, rank=2))
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()


@pytest.mark.parametrize(
    "field,value",
    [
        ("content_hash", "sha256:" + "b" * 64),
        ("content_hash", None),
        ("rendered_hash", "sha256:" + "c" * 64),
        ("scope_key", "changed"),
        ("origin", "pinned"),
        ("mode", "pinned_skill"),
        ("rank", 2),
        ("score", 2),
        ("query_terms", ["changed"]),
        ("applicability", {"status": "unknown"}),
        ("selection_policy_version", None),
    ],
)
async def test_v3_binding_fields_cannot_be_replaced(fencing_db, field, value):
    aggregate, identity = await setup(fencing_db, "data")
    async with fencing_db.session_factory() as session:
        row = binding(aggregate.run.id, identity)
        session.add(row)
        await session.commit()
        row_id = row.id
    async with fencing_db.session_factory() as session:
        stored = await session.get(RunSkillSelectionRecord, row_id)
        setattr(stored, field, value)
        with pytest.raises(ValueError, match="immutable"):
            await session.commit()
        await session.rollback()


async def test_legacy_binding_cannot_be_relabelled_as_new_proof(fencing_db):
    aggregate, identity = await setup(fencing_db, "data")
    async with fencing_db.session_factory() as session:
        row = RunSkillSelectionRecord(
            run_id=aggregate.run.id,
            skill_version_id=identity,
            mode="retrieval",
            rank=1,
            score=1,
            query_terms=[],
        )
        session.add(row)
        await session.commit()
        row_id = row.id
    async with fencing_db.session_factory() as session:
        stored = await session.get(RunSkillSelectionRecord, row_id)
        for key in [
            "content_hash",
            "rendered_hash",
            "scope_key",
            "applicability",
            "selection_policy_version",
            "origin",
        ]:
            setattr(stored, key, getattr(binding(aggregate.run.id, identity), key))
        with pytest.raises(ValueError, match="immutable"):
            await session.commit()
        await session.rollback()
