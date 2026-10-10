"""Exercise ordinary selection, freeze revalidation and immutable user categories."""

import pytest
import test_lease_fencing as fencing
from pydantic import ValidationError

from evoagent.config import Settings
from evoagent.core.context import ContextBuilder
from evoagent.db.models import SkillRecord, SkillVersionRecord, TaskRecord
from evoagent.retrieval.sources import load_source
from evoagent.runtime.context_resolver import ContextResolver
from evoagent.skills.applicability import VerifiedSkillFacts
from evoagent.skills.canonical import content_hash
from evoagent.skills.lifecycle import SkillVersionStatus
from evoagent.skills.retrieval import SkillRetrievalService
from evoagent.skills.schema import SkillDefinition
from evoagent.tasks.lease import JobLeaseManager
from evoagent.tasks.lease_guard import LeaseGuard
from evoagent.tasks.service import TaskService
from evoagent.tools.builtin.calculator import CalculatorTool
from evoagent.tools.registry import ToolRegistry

fencing_db = fencing.fencing_db


def body(v2=True):
    data = {
        "name": "calculate_report",
        "description": "calculate report",
        "triggers": ["calculate report"],
        "preconditions": {"allowed_tools": ["calculator"], "max_effective_risk": "R0"},
        "steps": [{"id": "compute", "action": "tool", "tool": "calculator", "args": {}}],
        "success_criteria": ["result"],
        "validators": ["result"],
    }
    if v2:
        data.update(
            schema_version=2,
            rationale="development example",
            stop_conditions=["stop on error"],
            counterexamples=[
                {
                    "situation": "non data task",
                    "why_not": "wrong category",
                    "origin": "hypothetical",
                }
            ],
            applicability={
                "task_families": ["data"],
                "required_facts": [
                    {"key": "tool.available", "op": "contains", "value": "calculator"}
                ],
                "excluded_conditions": [{"key": "project.available", "value": True}],
            },
        )
    return SkillDefinition.model_validate(data).model_dump(mode="json")


async def setup(db, family=None, v2=True):
    service = TaskService(db.session_factory)
    scope = await service.create_session("applicability")
    aggregate = await service.create_task(
        session_id=scope.id,
        goal="calculate report data",
        family=family,
        provider="mock",
        model="mock",
    )
    async with db.session_factory() as session:
        skill = SkillRecord(slug="calculate_report", name="calculate_report", description="report")
        session.add(skill)
        await session.flush()
        definition = body(v2)
        version = SkillVersionRecord(
            skill_id=skill.id,
            version=1,
            schema_version=definition["schema_version"],
            definition=definition,
            content_hash=content_hash(definition),
            extraction_key=content_hash({"skill": str(skill.id)}),
            lifecycle_status=SkillVersionStatus.ACTIVE,
        )
        session.add(version)
        await session.flush()
        skill.active_version_id = version.id
        await session.commit()
    return aggregate, version.id


@pytest.mark.parametrize("family,expected", [(None, False), ("research", False), ("data", True)])
@pytest.mark.parametrize("route", ["bm25", "resolver"])
async def test_user_category_filters_both_routes(fencing_db, tmp_path, family, expected, route):
    aggregate, identity = await setup(fencing_db, family)
    registry = ToolRegistry([CalculatorTool()])
    if route == "bm25":
        service = SkillRetrievalService(fencing_db.session_factory, registry, minimum_score=0)
        matches = await service.select(aggregate.run.id, aggregate.task.goal)
        assert bool(matches) is expected
        assert await service.select(aggregate.run.id, "different goal") == matches
    else:
        lease = await JobLeaseManager(fencing_db.session_factory, lease_seconds=60).claim_next(
            "test"
        )
        resolver = ContextResolver(
            fencing_db.session_factory,
            Settings(
                workspace=tmp_path, retrieval_backend="lexical", retrieval_min_lexical_score=0.001
            ),
            registry,
            LeaseGuard(lease),
            ContextBuilder(),
        )
        result = await resolver.resolve(aggregate.task, aggregate.run)
        assert bool(result.skills) is expected
        assert await resolver.resolve(aggregate.task, aggregate.run) == result
    if expected:
        assert (matches if route == "bm25" else result.skills)[0].document.version_id == identity


async def test_source_revalidation_does_not_bypass_applicability(fencing_db):
    _, identity = await setup(fencing_db, "data")
    facts = VerifiedSkillFacts(("calculator",), False, task_family="data")
    async with fencing_db.session_factory() as session:
        assert await load_source(session, f"skill:{identity}", applicability_facts=facts, lock=True)
        blocked = VerifiedSkillFacts(("calculator",), True, task_family="data")
        assert (
            await load_source(session, f"skill:{identity}", applicability_facts=blocked, lock=True)
            is None
        )


async def test_legacy_v1_does_not_require_new_category(fencing_db):
    aggregate, identity = await setup(fencing_db, v2=False)
    matches = await SkillRetrievalService(
        fencing_db.session_factory, ToolRegistry([CalculatorTool()]), minimum_score=0
    ).select(aggregate.run.id, aggregate.task.goal)
    assert matches[0].document.version_id == identity


async def test_family_is_frozen_and_rejects_unrecognized_labels(fencing_db):
    aggregate, _ = await setup(fencing_db, "data")
    async with fencing_db.session_factory() as session:
        task = await session.get(TaskRecord, aggregate.task.id)
        task.family = "research"
        with pytest.raises(ValueError, match="immutable"):
            await session.commit()
        await session.rollback()
    with pytest.raises(ValidationError):
        await TaskService(fencing_db.session_factory).create_task(
            session_id=aggregate.task.session_id,
            goal="report",
            provider="mock",
            model="mock",
            family="guessed-from-model",
        )


@pytest.mark.parametrize("route", ["bm25", "resolver"])
async def test_foreign_workspace_cannot_select_default_workspace_skill(fencing_db, tmp_path, route):
    from sqlalchemy import select

    from evoagent.db.models import RunSkillSelectionRecord

    _, identity = await setup(fencing_db, "data")
    service = TaskService(fencing_db.session_factory)
    workspace = await service.create_workspace("other workspace")
    scope = await service.create_session("other", workspace_id=workspace.id)
    aggregate = await service.create_task(
        session_id=scope.id, goal="calculate report", family="data", provider="mock", model="mock"
    )
    registry = ToolRegistry([CalculatorTool()])
    if route == "bm25":
        assert (
            await SkillRetrievalService(fencing_db.session_factory, registry).select(
                aggregate.run.id, aggregate.task.goal
            )
            == ()
        )
    else:
        # Claim order does not matter: finalize the unused first task before
        # claiming this one so the resolver receives its own fenced Run.
        manager = JobLeaseManager(fencing_db.session_factory, lease_seconds=60)
        first = await manager.claim_next("other")
        from evoagent.tasks.lease import TaskExecutionResult
        from evoagent.tasks.state_machine import PersistentRunStatus

        await manager.finalize(first, TaskExecutionResult(status=PersistentRunStatus.COMPLETED))
        lease = await manager.claim_next("other")
        resolver = ContextResolver(
            fencing_db.session_factory,
            Settings(workspace=tmp_path),
            registry,
            LeaseGuard(lease),
            ContextBuilder(),
        )
        assert not (await resolver.resolve(aggregate.task, aggregate.run)).skills
    async with fencing_db.session_factory() as session:
        assert await load_source(session, f"skill:{identity}", scope_id=scope.id) is None
        assert not list(
            await session.scalars(
                select(RunSkillSelectionRecord).where(
                    RunSkillSelectionRecord.run_id == aggregate.run.id
                )
            )
        )


@pytest.mark.parametrize("route", ["bm25", "resolver"])
async def test_scope_change_of_selected_skill_stops_restore(fencing_db, tmp_path, route):
    from evoagent.memory.schema import MemoryError

    aggregate, identity = await setup(fencing_db, "data")
    registry = ToolRegistry([CalculatorTool()])
    if route == "bm25":
        retrieval = SkillRetrievalService(fencing_db.session_factory, registry)
        assert await retrieval.select(aggregate.run.id, aggregate.task.goal)

        async def restore():
            return await retrieval.select(aggregate.run.id, aggregate.task.goal)
    else:
        lease = await JobLeaseManager(fencing_db.session_factory, lease_seconds=60).claim_next(
            "test"
        )
        resolver = ContextResolver(
            fencing_db.session_factory,
            Settings(workspace=tmp_path),
            registry,
            LeaseGuard(lease),
            ContextBuilder(),
        )
        assert (await resolver.resolve(aggregate.task, aggregate.run)).skills

        async def restore():
            return await resolver.resolve(aggregate.task, aggregate.run)

    other = await TaskService(fencing_db.session_factory).create_workspace("moved scope")
    async with fencing_db.session_factory() as session:
        version = await session.get(SkillVersionRecord, identity)
        skill = await session.get(SkillRecord, version.skill_id)
        skill.workspace_id = other.id
        await session.commit()
    with pytest.raises(MemoryError, match="context_source_revoked"):
        await restore()


async def test_project_scoped_skill_requires_the_frozen_task_project(fencing_db, tmp_path):
    from evoagent.db.models import ProjectRecord, SessionRecord

    aggregate, identity = await setup(fencing_db, "data")
    async with fencing_db.session_factory() as session:
        project = ProjectRecord(name="project", root=str(tmp_path))
        session.add(project)
        await session.flush()
        version = await session.get(SkillVersionRecord, identity)
        skill = await session.get(SkillRecord, version.skill_id)
        skill.project_id = project.id
        scope = await session.get(SessionRecord, aggregate.task.session_id)
        # Changing the session project must not grant an old Task a new scope.
        scope.project_id = project.id
        await session.commit()
    async with fencing_db.session_factory() as session:
        assert (
            await load_source(session, f"skill:{identity}", scope_id=aggregate.task.session_id)
            is None
        )
        assert await load_source(
            session, f"skill:{identity}", scope_id=aggregate.task.session_id, project_id=project.id
        )
    assert not await SkillRetrievalService(
        fencing_db.session_factory, ToolRegistry([CalculatorTool()])
    ).select(aggregate.run.id, aggregate.task.goal)


@pytest.mark.parametrize("change_at", ["tool", "model"])
async def test_scope_revoked_blocks_next_call(fencing_db, tmp_path, change_at):
    from evoagent.core.models import (
        FinishReason,
        Message,
        MessageRole,
        ModelResponse,
        ProviderEventType,
        ToolCall,
    )
    from evoagent.providers.mock import MockProvider
    from evoagent.runtime.persistent_runner import PersistentAgentRunner
    from evoagent.tasks.state_machine import PersistentRunStatus

    aggregate, identity = await setup(fencing_db, "data")
    other = await TaskService(fencing_db.session_factory).create_workspace("moved")

    calls = []

    async def move_scope():
        async with fencing_db.session_factory() as session:
            version = await session.get(SkillVersionRecord, identity)
            skill = await session.get(SkillRecord, version.skill_id)
            skill.workspace_id = other.id
            await session.commit()

    class MoveScope(CalculatorTool):
        async def invoke(self, arguments):
            calls.append("calculate")
            result = await super().invoke(arguments)
            if change_at == "tool":
                await move_scope()
            return result

    class ScopeProvider(MockProvider):
        async def stream(self, request):
            async for event in super().stream(request):
                if change_at == "model" and event.type is ProviderEventType.COMPLETED:
                    await move_scope()
                yield event

    provider = ScopeProvider(
        [
            ModelResponse(
                message=Message(
                    role=MessageRole.ASSISTANT,
                    tool_calls=(
                        ToolCall(
                            call_id="calculate", name="calculator", arguments={"expression": "2+2"}
                        ),
                    ),
                ),
                finish_reason=FinishReason.TOOL_CALLS,
            ),
            ModelResponse(
                message=Message(role=MessageRole.ASSISTANT, content="should not run"),
                finish_reason=FinishReason.STOP,
            ),
        ]
    )
    manager = JobLeaseManager(fencing_db.session_factory, lease_seconds=60)
    lease = await manager.claim_next("boundary")
    runner = PersistentAgentRunner(
        settings=Settings(workspace=tmp_path),
        session_factory=fencing_db.session_factory,
        context_builder=ContextBuilder(),
        provider=provider,
        registry=ToolRegistry([MoveScope()]),
    )
    result = await runner.handle(lease)
    assert result.status == PersistentRunStatus.FAILED
    assert result.error_code == "skill_source_revoked"
    assert len(provider.requests) == 1 and provider.remaining_steps == 1
    assert calls == (["calculate"] if change_at == "tool" else [])
    await manager.finalize(lease, result)
    restored = await TaskService(fencing_db.session_factory).get_task(aggregate.task.id)
    assert restored.run.error_code == "skill_source_revoked"


async def test_formal_binding_checks_are_per_request_not_per_stream_delta(fencing_db, monkeypatch):
    from evoagent.core.events import ProgressEventType
    from evoagent.core.models import EventType
    from evoagent.skills.selection import FormalSkillReader
    from evoagent.trace.persistent_sink import PersistentEventSink

    await setup(fencing_db, "data")
    lease = await JobLeaseManager(fencing_db.session_factory, lease_seconds=60).claim_next("test")
    checks = []

    async def check(_self, _session, *, task, run):
        checks.append((task.id, run.id))

    monkeypatch.setattr(FormalSkillReader, "check_run_bindings", check)
    sink = PersistentEventSink(
        lease.run_id, fencing_db.session_factory, lease_guard=LeaseGuard(lease), batch_progress=True
    )
    try:
        await sink.emit(EventType.MODEL_REQUESTED)
        for _ in range(40):
            await sink.append_progress(ProgressEventType.MODEL_DELTA, {"text": "chunk"})
        await sink.flush()
        await sink.emit(EventType.MODEL_COMPLETED)
        assert checks == [(lease.task_id, lease.run_id)]
    finally:
        await sink.aclose()


async def test_runner_uses_frozen_rendered_text_without_invoking_renderer(
    fencing_db, tmp_path, monkeypatch
):
    from evoagent.core.models import FinishReason, Message, MessageRole, ModelResponse
    from evoagent.providers.mock import MockProvider
    from evoagent.runtime.persistent_runner import PersistentAgentRunner
    from evoagent.skills.rendering import SkillContextRenderer
    from evoagent.tasks.state_machine import PersistentRunStatus

    aggregate, _ = await setup(fencing_db, "data")
    registry = ToolRegistry([CalculatorTool()])
    settings = Settings(
        workspace=tmp_path,
        retrieval_backend="lexical",
        memory_retrieval_enabled=True,
        retrieval_min_lexical_score=0.001,
    )
    manager = JobLeaseManager(fencing_db.session_factory, lease_seconds=60)
    lease = await manager.claim_next("frozen-text")
    resolver = ContextResolver(
        fencing_db.session_factory, settings, registry, LeaseGuard(lease), ContextBuilder()
    )
    frozen = await resolver.resolve(aggregate.task, aggregate.run)
    assert frozen.skill_text and frozen.skills

    def unavailable(*_args, **_kwargs):
        raise AssertionError("frozen context must not invoke the renderer")

    monkeypatch.setattr(SkillContextRenderer, "render", unavailable)
    provider = MockProvider(
        [
            ModelResponse(
                message=Message(role=MessageRole.ASSISTANT, content="finished"),
                finish_reason=FinishReason.STOP,
            )
        ]
    )
    runner = PersistentAgentRunner(
        settings=settings,
        session_factory=fencing_db.session_factory,
        context_builder=ContextBuilder(),
        provider=provider,
        registry=registry,
    )
    result = await runner.handle(lease)
    assert result.status == PersistentRunStatus.COMPLETED, result.error_code
    assert len(provider.requests) == 1
    assert any(
        frozen.skill_text in (message.content or "") for message in provider.requests[0].messages
    )
    await manager.finalize(lease, result)
