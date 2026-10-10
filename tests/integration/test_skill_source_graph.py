"""Actual personal provenance, including revision lineage outside selected parents."""

from copy import deepcopy
from uuid import uuid4

import pytest
from sqlalchemy import select

from evoagent.db.models import SkillRecord, SkillSourceRecord, SkillVersionRecord
from evoagent.skills.access import SkillAccessError, SkillAccessPolicy
from evoagent.skills.canonical import content_hash
from evoagent.skills.lifecycle import SkillStatus, SkillVersionStatus

pytest_plugins = ("tests.integration.test_personal_trials",)


async def revision(context, *, foreign_skill=False, self_parent=False):
    _, db, _, _, original, skill = context
    async with db.session_factory() as session:
        link = await session.scalar(
            select(SkillSourceRecord).where(SkillSourceRecord.skill_version_id == original.id)
        )
        owner = skill.id
        if foreign_skill:
            foreign = SkillRecord(
                workspace_id=skill.workspace_id,
                slug="foreign_revision",
                name="foreign",
                description="fixture",
            )
            session.add(foreign)
            await session.flush()
            owner = foreign.id
        identity = uuid4()
        child = SkillVersionRecord(
            id=identity,
            skill_id=owner,
            parent_version_id=identity if self_parent else original.id,
            version=2,
            schema_version=original.schema_version,
            definition=deepcopy(original.definition),
            content_hash=original.content_hash,
            extraction_key=content_hash({"fixture": str(uuid4())}),
            lifecycle_status=SkillVersionStatus.DRAFT,
        )
        session.add(child)
        await session.flush()
        session.add(
            SkillSourceRecord(
                skill_version_id=child.id,
                source_run_id=link.source_run_id,
                source_kind=link.source_kind,
                learning_source_id=link.learning_source_id,
                trace_artifact_id=link.trace_artifact_id,
                source_trace_hash=link.source_trace_hash,
            )
        )
        await session.commit()
        return child


@pytest.mark.parametrize(
    "options,expected",
    [
        ({"foreign_skill": True}, "skill_revision_parent_invalid"),
        ({"self_parent": True}, "skill_source_graph_cycle"),
    ],
)
async def test_invalid_revision_lineage_is_refused(trial_candidate, options, expected):
    _, db, _, _, _, skill = trial_candidate
    child = await revision(trial_candidate, **options)
    async with db.session_factory() as session:
        with pytest.raises(SkillAccessError, match=expected):
            await SkillAccessPolicy().check(session, child.id, workspace_id=skill.workspace_id)


@pytest.mark.parametrize("state", ["draft", "retired"])
async def test_revision_lineage_accepts_valid_frozen_parent(trial_candidate, state):
    _, db, _, _, parent, skill = trial_candidate
    child = await revision(trial_candidate)
    async with db.session_factory() as session:
        row = await session.get(SkillVersionRecord, parent.id)
        row.lifecycle_status = SkillVersionStatus(state)
        await session.commit()
    async with db.session_factory() as session:
        result = await SkillAccessPolicy().check(session, child.id, workspace_id=skill.workspace_id)
        assert result.id == child.id


@pytest.mark.parametrize("target", ["root", "revision_parent"])
@pytest.mark.parametrize("revocation", ["disabled", "deprecated", "rejected"])
async def test_explicit_revocation_blocks_root_and_revision_parent(
    trial_candidate, target, revocation
):
    _, db, _, _, parent, skill = trial_candidate
    child = await revision(trial_candidate)
    checked = parent if target == "root" else child
    async with db.session_factory() as session:
        if revocation == "rejected":
            row = await session.get(SkillVersionRecord, parent.id)
            row.lifecycle_status = SkillVersionStatus.REJECTED
        else:
            row = await session.get(SkillRecord, skill.id)
            row.status = SkillStatus(revocation)
        await session.commit()
    async with db.session_factory() as session:
        with pytest.raises(SkillAccessError, match="skill_source_version_revoked"):
            await SkillAccessPolicy().check(session, checked.id, workspace_id=skill.workspace_id)


async def test_revision_ancestry_uses_the_same_32_node_budget(trial_candidate):
    _, db, _, _, original, skill = trial_candidate
    async with db.session_factory() as session:
        link = await session.scalar(
            select(SkillSourceRecord).where(SkillSourceRecord.skill_version_id == original.id)
        )
        previous = original.id
        for number in range(2, 34):
            child = SkillVersionRecord(
                skill_id=skill.id,
                parent_version_id=previous,
                version=number,
                schema_version=original.schema_version,
                definition=deepcopy(original.definition),
                content_hash=original.content_hash,
                extraction_key=content_hash({"fixture": str(uuid4())}),
                lifecycle_status=SkillVersionStatus.DRAFT,
            )
            session.add(child)
            await session.flush()
            session.add(
                SkillSourceRecord(
                    skill_version_id=child.id,
                    source_run_id=link.source_run_id,
                    source_kind=link.source_kind,
                    learning_source_id=link.learning_source_id,
                    trace_artifact_id=link.trace_artifact_id,
                    source_trace_hash=link.source_trace_hash,
                )
            )
            previous = child.id
            if number == 32:
                within_budget = child.id
        await session.commit()
    async with db.session_factory() as session:
        assert (
            await SkillAccessPolicy().check(session, within_budget, workspace_id=skill.workspace_id)
        ).id == within_budget
        with pytest.raises(SkillAccessError, match="skill_source_graph_budget_exceeded"):
            await SkillAccessPolicy().check(session, previous, workspace_id=skill.workspace_id)
