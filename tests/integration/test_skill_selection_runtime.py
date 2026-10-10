"""Ordinary selector persistence tests do not activate the production route."""

from uuid import uuid4

import pytest
from sqlalchemy import func, select

from evoagent.db.models import (
    RetrievalBatchRecord,
    RunRecord,
    RunSkillSelectionRecord,
    SkillRecord,
    SkillVersionRecord,
    TaskRecord,
)
from evoagent.memory.schema import MemoryError
from evoagent.skills.rendering import SkillContextRenderer
from evoagent.skills.selection import SkillSelector
from evoagent.skills.selection_runtime import resolve_ordinary
from evoagent.skills.selection_snapshot import SkillSelectionScope
from evoagent.tasks.lease import JobLeaseManager
from evoagent.tasks.lease_guard import LeaseGuard, LeaseLostError
from evoagent.tools.builtin.file_read import FileReadTool
from evoagent.tools.registry import ToolRegistry
from tests.integration.test_skill_selector_candidates import formal_fixture

pytest_plugins = ("tests.integration.test_personal_trials",)


async def setup_run(context, settings, *, mode="retrieval", family="general", legacy=False):
    _, db, source_run, _, _, skill = context
    async with db.session_factory() as session:
        original = await session.get(RunRecord, source_run)
        source_task = await session.get(TaskRecord, original.task_id)
        task = TaskRecord(
            session_id=source_task.session_id,
            selection_contract_version=None if legacy else 3,
            goal="核验 验证 检查",
            family=family,
            status="queued",
        )
        session.add(task)
        await session.flush()
        run = RunRecord(
            task_id=task.id, provider="mock", model="mock", run_mode=mode, data_role="personal"
        )
        session.add(run)
        await session.commit()
    lease = await JobLeaseManager(db.session_factory, lease_seconds=60).claim_next("selector-test")
    assert lease is not None and lease.run_id == run.id
    instance = SkillSelector(
        db.session_factory,
        settings,
        ToolRegistry([FileReadTool(settings.workspace)]),
        LeaseGuard(lease),
        SkillSelectionScope(workspace_id=skill.workspace_id),
    )
    return instance, task, run


async def test_actual_formal_choice_and_renderer_free_restore(
    trial_candidate, learning_api, monkeypatch
):
    await formal_fixture(trial_candidate)
    _, db, _, settings = learning_api
    instance, task, run = await setup_run(trial_candidate, settings)
    first = await resolve_ordinary(instance, task, run)
    assert len(first.selections) == 1 and first.selections[0].origin == "formal"
    assert first.text and first.selection_hash
    async with db.session_factory() as session:
        binding = await session.scalar(
            select(RunSkillSelectionRecord).where(RunSkillSelectionRecord.run_id == run.id)
        )
        assert binding.content_hash == first.selections[0].content_hash
        assert binding.rendered_hash == first.selections[0].rendered_hash
        assert binding.applicability["status"] == "applicable"

    def forbidden_render(*_):
        raise AssertionError("restoration must use persisted text")

    monkeypatch.setattr(SkillContextRenderer, "render", forbidden_render)
    # Pointer changes do not rewrite already-frozen method text.
    async with db.session_factory() as session:
        version = await session.get(SkillVersionRecord, first.selections[0].version_id)
        version.lifecycle_status = "retired"
        skill = await session.get(SkillRecord, version.skill_id)
        skill.active_version_id = None
        await session.commit()
    restored = await resolve_ordinary(instance, task, run)
    assert restored == first
    async with db.session_factory() as session:
        assert (
            await session.scalar(
                select(func.count())
                .select_from(RunSkillSelectionRecord)
                .where(RunSkillSelectionRecord.run_id == run.id)
            )
            == 1
        )
        assert (
            await session.scalar(
                select(func.count())
                .select_from(RetrievalBatchRecord)
                .where(RetrievalBatchRecord.run_id == run.id)
            )
            == 1
        )
    async with db.session_factory() as session:
        skill = await session.get(SkillRecord, trial_candidate[5].id)
        skill.status = "disabled"
        await session.commit()
    with pytest.raises(MemoryError, match="source_revoked"):
        await resolve_ordinary(instance, task, run)


@pytest.mark.parametrize("mode,family", [("baseline", "general"), ("retrieval", "coding")])
async def test_zero_choice_is_frozen_and_restored(trial_candidate, learning_api, mode, family):
    await formal_fixture(trial_candidate)
    _, db, _, settings = learning_api
    instance, task, run = await setup_run(trial_candidate, settings, mode=mode, family=family)
    choice = await resolve_ordinary(instance, task, run)
    assert choice.selections == () and choice.text is None
    assert await resolve_ordinary(instance, task, run) == choice
    async with db.session_factory() as session:
        assert (await session.get(RunRecord, run.id)).skill_selection_frozen


async def test_expired_owner_cannot_freeze_after_scanning(
    trial_candidate, learning_api, monkeypatch
):
    await formal_fixture(trial_candidate)
    _, db, _, settings = learning_api
    instance, task, run = await setup_run(trial_candidate, settings)
    original = instance._verify_text

    async def fence(*args):
        await original(*args)
        async with db.session_factory() as session:
            row = await session.get(TaskRecord, task.id)
            row.lease_epoch += 1
            await session.commit()

    monkeypatch.setattr(instance, "_verify_text", fence)
    with pytest.raises(LeaseLostError):
        await resolve_ordinary(instance, task, run)
    async with db.session_factory() as session:
        assert not (await session.get(RunRecord, run.id)).skill_selection_frozen
        assert (
            await session.scalar(
                select(func.count())
                .select_from(RunSkillSelectionRecord)
                .where(RunSkillSelectionRecord.run_id == run.id)
            )
            == 0
        )


async def test_legacy_queued_task_and_foreign_scope_cannot_be_upgraded(
    trial_candidate, learning_api
):
    _, _, _, settings = learning_api
    instance, task, run = await setup_run(trial_candidate, settings, legacy=True)
    with pytest.raises(MemoryError, match="scope_invalid"):
        await resolve_ordinary(instance, task, run)
    instance.scope = SkillSelectionScope(workspace_id=uuid4())
    with pytest.raises(MemoryError, match="scope_invalid"):
        await resolve_ordinary(instance, task, run)
