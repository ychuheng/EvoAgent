"""Actual frozen bindings, with synthetic config/gate fixtures, recheck provenance."""

import pytest
from sqlalchemy import select

from evoagent.db.models import LearningSourceRecord, RunRecord, SkillRecord, TaskRecord
from evoagent.memory.schema import MemoryError
from evoagent.runtime.run_config import RunConfigSnapshot, RunMode, sha256_text
from evoagent.skills.selection_boundary import check_ordinary_bindings
from evoagent.skills.selection_runtime import resolve_ordinary
from tests.integration.test_skill_selection_runtime import setup_run
from tests.integration.test_skill_selector_candidates import formal_fixture

pytest_plugins = ("tests.integration.test_personal_trials",)


def config_for(run, choice):
    selected = choice.selections[0] if choice.selections else None
    return RunConfigSnapshot(
        schema_version=3,
        provider=run.provider,
        model=run.model,
        system_prompt_hash=sha256_text("synthetic system"),
        tool_manifest_hash=sha256_text("synthetic tools"),
        policy_hash=sha256_text("synthetic policy"),
        max_iterations=8,
        max_total_tokens=32000,
        model_timeout_seconds=30,
        task_timeout_seconds=60,
        tool_timeout_seconds=10,
        code_version="boundary-fixture",
        run_mode=RunMode(run.run_mode),
        selector_version="skill-selector-v1",
        renderer_version=2,
        skill_renderer_version=2,
        selected_skills=choice.selections,
        skill_selection_hash=choice.selection_hash,
        skill_version_id=selected.version_id if selected else None,
        skill_content_hash=selected.content_hash if selected else None,
        skill_context_hash=selected.rendered_hash if selected else None,
    )


async def test_boundary_rechecks_frozen_source_and_exact_config(trial_candidate, learning_api):
    await formal_fixture(trial_candidate)
    _, db, _, settings = learning_api
    selector, task, run = await setup_run(trial_candidate, settings)
    choice = await resolve_ordinary(selector, task, run)
    config = config_for(run, choice)
    async with db.session_factory() as session:
        current = await session.get(RunRecord, run.id)
        current.config_snapshot = config.canonical_dict()
        current.config_hash = config.content_hash()
        await session.commit()
    async with db.session_factory() as session:
        current = await session.get(RunRecord, run.id)
        await check_ordinary_bindings(session, task=task, run=current)
        source = await session.scalar(select(LearningSourceRecord))
        source.status = "revoked"
        await session.commit()
    async with db.session_factory() as session:
        current = await session.get(RunRecord, run.id)
        with pytest.raises(MemoryError, match="source_revoked"):
            await check_ordinary_bindings(session, task=task, run=current)


async def test_boundary_refuses_tampered_config_even_with_zero_selection(
    trial_candidate, learning_api
):
    _, db, _, settings = learning_api
    selector, task, run = await setup_run(trial_candidate, settings, mode="baseline")
    choice = await resolve_ordinary(selector, task, run)
    config = config_for(run, choice)
    async with db.session_factory() as session:
        current = await session.get(RunRecord, run.id)
        current.config_snapshot = config.canonical_dict()
        current.config_hash = config.content_hash()
        await session.commit()
    async with db.session_factory() as session:
        current = await session.get(RunRecord, run.id)
        await check_ordinary_bindings(session, task=task, run=current)
        # Isolated corrupt identity; no production endpoint changes frozen hash.
        current.config_hash = "sha256:" + "a" * 64
        await session.commit()
    async with db.session_factory() as session:
        current = await session.get(RunRecord, run.id)
        with pytest.raises(MemoryError, match="selection_corrupt"):
            await check_ordinary_bindings(session, task=task, run=current)


async def test_marked_task_reaches_model_with_actual_formal_context(trial_candidate, learning_api):
    from evoagent.core.context import ContextBuilder
    from evoagent.core.models import FinishReason, Message, MessageRole, ModelResponse
    from evoagent.providers.mock import MockProvider
    from evoagent.runtime.persistent_runner import PersistentAgentRunner
    from evoagent.tasks.state_machine import PersistentRunStatus

    await formal_fixture(trial_candidate)
    _, db, _, settings = learning_api
    selector, _, run = await setup_run(trial_candidate, settings)
    provider = MockProvider(
        [
            ModelResponse(
                message=Message(role=MessageRole.ASSISTANT, content="offline done"),
                finish_reason=FinishReason.STOP,
            )
        ]
    )
    runner = PersistentAgentRunner(
        settings=settings,
        session_factory=db.session_factory,
        context_builder=ContextBuilder(),
        provider=provider,
        registry=selector.registry,
    )
    result = await runner.handle(selector.guard.lease)
    assert result.status is PersistentRunStatus.COMPLETED
    assert len(provider.requests) == 1
    assert any("Skill" in (item.content or "") for item in provider.requests[0].messages)
    async with db.session_factory() as session:
        current = await session.get(RunRecord, run.id)
        assert current.config_snapshot["schema_version"] == 3
        assert current.config_snapshot["selected_skills"][0]["origin"] == "formal"
        await check_ordinary_bindings(
            session, task=await session.get(TaskRecord, run.task_id), run=current
        )


@pytest.mark.parametrize("change", ["source", "scope"])
async def test_source_revoked_by_tool_stops_the_next_model_turn(
    trial_candidate, learning_api, change
):
    from evoagent.core.context import ContextBuilder
    from evoagent.core.models import FinishReason, Message, MessageRole, ModelResponse, ToolCall
    from evoagent.providers.mock import MockProvider
    from evoagent.runtime.persistent_runner import PersistentAgentRunner
    from evoagent.tasks.service import TaskService
    from evoagent.tasks.state_machine import PersistentRunStatus
    from evoagent.tools.builtin.calculator import CalculatorTool

    await formal_fixture(trial_candidate)
    _, db, _, settings = learning_api
    selector, _, _ = await setup_run(trial_candidate, settings)
    other = await TaskService(db.session_factory).create_workspace("foreign-scope")

    class Revoker(CalculatorTool):
        async def invoke(self, arguments):
            result = await super().invoke(arguments)
            async with db.session_factory() as session:
                if change == "source":
                    source = await session.scalar(select(LearningSourceRecord))
                    source.status = "revoked"
                else:
                    skill = await session.scalar(select(SkillRecord))
                    skill.workspace_id = other.id
                await session.commit()
            return result

    selector.registry.register(Revoker())
    provider = MockProvider(
        [
            ModelResponse(
                message=Message(
                    role=MessageRole.ASSISTANT,
                    tool_calls=(
                        ToolCall(
                            call_id="revoke-between-turns",
                            name="calculator",
                            arguments={"expression": "1+1"},
                        ),
                    ),
                ),
                finish_reason=FinishReason.TOOL_CALLS,
            ),
            ModelResponse(
                message=Message(role=MessageRole.ASSISTANT, content="must not request this"),
                finish_reason=FinishReason.STOP,
            ),
        ]
    )
    runner = PersistentAgentRunner(
        settings=settings,
        session_factory=db.session_factory,
        context_builder=ContextBuilder(),
        provider=provider,
        registry=selector.registry,
    )
    result = await runner.handle(selector.guard.lease)
    assert result.status is PersistentRunStatus.FAILED
    assert result.error_code == "skill_source_revoked"
    assert len(provider.requests) == 1 and provider.remaining_steps == 1


async def test_requested_cancel_is_seen_before_the_next_model_call(trial_candidate, learning_api):
    import asyncio

    from evoagent.core.context import ContextBuilder
    from evoagent.core.models import FinishReason, Message, MessageRole, ModelResponse, ToolCall
    from evoagent.providers.mock import MockProvider
    from evoagent.runtime.persistent_runner import PersistentAgentRunner
    from evoagent.tools.builtin.calculator import CalculatorTool

    _, db, _, settings = learning_api
    selector, task, _ = await setup_run(trial_candidate, settings)

    class Cancel(CalculatorTool):
        async def invoke(self, arguments):
            result = await super().invoke(arguments)
            async with db.session_factory() as session:
                row = await session.get(TaskRecord, task.id)
                row.cancel_requested = True
                await session.commit()
            return result

    selector.registry.register(Cancel())
    provider = MockProvider(
        [
            ModelResponse(
                message=Message(
                    role=MessageRole.ASSISTANT,
                    tool_calls=(
                        ToolCall(
                            call_id="cancel-before-next-turn",
                            name="calculator",
                            arguments={"expression": "1+1"},
                        ),
                    ),
                ),
                finish_reason=FinishReason.TOOL_CALLS,
            ),
            ModelResponse(
                message=Message(role=MessageRole.ASSISTANT, content="unreachable"),
                finish_reason=FinishReason.STOP,
            ),
        ]
    )
    runner = PersistentAgentRunner(
        settings=settings,
        session_factory=db.session_factory,
        context_builder=ContextBuilder(),
        provider=provider,
        registry=selector.registry,
    )
    with pytest.raises(asyncio.CancelledError):
        await runner.handle(selector.guard.lease)
    assert len(provider.requests) == 1 and provider.remaining_steps == 1


async def test_new_task_service_and_configured_handler_adopt_without_manual_marker(
    trial_candidate, learning_api, tmp_path, monkeypatch
):
    from evoagent.core.models import FinishReason, Message, MessageRole, ModelResponse
    from evoagent.projects.service import ProjectService
    from evoagent.providers.mock import MockProvider
    from evoagent.tasks.lease import JobLeaseManager
    from evoagent.tasks.service import TaskService
    from evoagent.tasks.state_machine import PersistentRunStatus
    from evoagent.workers.bootstrap import ConfiguredTaskHandler

    await formal_fixture(trial_candidate)
    _, db, _, settings = learning_api
    root = tmp_path / "authorized-adoption-project"
    root.mkdir()
    (root / "README.md").write_text("# controlled project\n", encoding="utf8")
    project = await ProjectService(db.session_factory).register(path=str(root))
    tasks = TaskService(db.session_factory)
    chat = await tasks.create_session("new entry")
    aggregate = await tasks.create_task(
        session_id=chat.id,
        goal="核验 验证 检查",
        family="general",
        provider="mock",
        model=settings.model or "mock-model",
        project_id=project.id,
    )
    assert aggregate.task.selection_contract_version == 3
    provider = MockProvider(
        [
            ModelResponse(
                message=Message(role=MessageRole.ASSISTANT, content="offline actual adoption"),
                finish_reason=FinishReason.STOP,
            )
        ]
    )
    monkeypatch.setattr(ConfiguredTaskHandler, "_provider", lambda *_: provider)
    lease = await JobLeaseManager(db.session_factory, lease_seconds=60).claim_next("new-entry")
    assert lease and lease.run_id == aggregate.run.id
    result = await ConfiguredTaskHandler(settings, db).handle(lease)
    assert result.status is PersistentRunStatus.COMPLETED
    assert len(provider.requests) == 1
    async with db.session_factory() as session:
        current = await session.get(RunRecord, aggregate.run.id)
        assert current.config_snapshot["schema_version"] == 3
        selected = current.config_snapshot["selected_skills"]
        assert len(selected) == 1 and selected[0]["origin"] == "formal"
        assert selected[0]["scope"]["project_id"] == str(project.id)
        assert selected[0]["version_id"] == str(trial_candidate[4].id)
