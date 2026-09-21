import asyncio
import json
from copy import deepcopy
from uuid import uuid4

import pytest
from mcp import types
from sqlalchemy import select
from test_mcp_catalogs import (
    create_enabled,
    write_state,
)
from test_mcp_catalogs import (
    environment as environment,
)

from evoagent.core.events import InMemoryEventSink
from evoagent.core.models import ToolCall, ToolRisk
from evoagent.db.models import (
    MCPToolReviewRecord,
    ToolCallRecord,
    ToolEffectRecord,
    ToolEffectStatus,
)
from evoagent.mcp.adapter import (
    MCPExecutionError,
    MCPToolAdapter,
    mapped_name,
    register_run_tools,
)
from evoagent.mcp.schema import ExecutionUpdate, MCPError, ToolReview
from evoagent.tasks.lease import JobLeaseManager
from evoagent.tasks.lease_guard import LeaseGuard
from evoagent.tasks.service import TaskService
from evoagent.tools.approvals import ApprovalRequiredError, ApprovalService
from evoagent.tools.effects import PersistentToolMiddleware
from evoagent.tools.executor import ToolExecutor
from evoagent.tools.policy import PermissionPolicy
from evoagent.tools.registry import ToolRegistry


async def setup_execution(environment, *, write=False, **state_options):
    db, settings, manager, service, state = environment
    log = state.with_suffix(".calls")
    write_state(state, 1, call_log=str(log), **state_options)
    server = await create_enabled(service)
    catalog = await service.discover(server["id"])
    await service.review(
        catalog["id"],
        ToolReview(
            expected_lock_version=0,
            tool_name="echo",
            approved=True,
            risk="R3" if write else "R0",
            effect="non_idempotent_write" if write else "read_only",
            reviewer="operator",
            reason="fixture acceptance",
        ),
    )
    await service.set_execution(
        server["id"],
        ExecutionUpdate(expected_lock_version=0, state="active", catalog_id=str(catalog["id"])),
    )
    tasks = TaskService(db.session_factory)
    session = await tasks.create_session("module 9")
    aggregate = await tasks.create_task(
        session_id=session.id, goal="echo", provider="mock", model="mock"
    )
    lease = await JobLeaseManager(db.session_factory, lease_seconds=120).claim_next("mcp-test")
    guard = LeaseGuard(lease)
    registry = ToolRegistry()
    await register_run_tools(service, registry, aggregate.run.id, guard)
    adapter = registry.get(registry.names[0])
    middleware = PersistentToolMiddleware(
        task_id=aggregate.task.id,
        run_id=aggregate.run.id,
        session_factory=db.session_factory,
        policy=PermissionPolicy(),
        lease_guard=guard,
    )
    executor = ToolExecutor(
        registry,
        InMemoryEventSink(aggregate.run.id),
        timeout_seconds=10,
        max_result_chars=2000,
        middleware=middleware,
    )
    return server, catalog, aggregate, guard, registry, adapter, executor, log


def call(adapter, identity="one", **arguments):
    return ToolCall(call_id=identity, name=adapter.name, arguments=arguments)


async def test_real_sdk_call_canonical_trace_and_manifest(environment):
    db, _, _, service, _ = environment
    _, _, aggregate, guard, registry, adapter, executor, log = await setup_execution(environment)
    result = await executor.execute(call(adapter, text="hello"))
    assert result.error_code is None
    assert json.loads(result.content) == {"text": ["hello"]}
    assert json.loads(log.read_text())["arguments"] == {"text": "hello"}
    assert registry.manifest()[0]["parameters"] == adapter.definition().parameters
    assert "payload" not in registry.manifest()[0]["parameters"]
    async with db.session_factory() as session:
        trace = await session.scalar(select(ToolCallRecord))
        assert trace.arguments == {"text": "hello"}
        assert trace.execution_binding["catalog_id"] == adapter.binding["catalog_id"]
    recovered = ToolRegistry()
    await register_run_tools(service, recovered, aggregate.run.id, guard)
    assert recovered.manifest_hash() == registry.manifest_hash()


async def test_invalid_arguments_and_no_middleware_never_call(environment):
    _, _, _, _, _, adapter, executor, log = await setup_execution(environment)
    adapter.spec["input_schema"] = {
        "type": "object",
        "required": ["text"],
        "properties": {"text": {"type": "string"}},
    }
    assert (await executor.execute(call(adapter, text=42))).error_code == "invalid_arguments"
    unprotected = ToolExecutor(
        ToolRegistry([adapter]), InMemoryEventSink(uuid4()), timeout_seconds=1, max_result_chars=100
    )
    assert (
        await unprotected.execute(call(adapter, text="x"))
    ).error_code == "mcp_persistence_required"
    assert not log.exists()


async def test_review_upgrade_invalidates_approval_and_keeps_unknown_key(environment):
    db, _, _, service, _ = environment
    _, catalog, aggregate, guard, _, adapter, executor, log = await setup_execution(
        environment, write=True
    )
    assert adapter.risk is ToolRisk.R3  # readOnlyHint=true 不降级
    with pytest.raises(ApprovalRequiredError) as pending:
        await executor.execute(call(adapter, text="write"))
    await ApprovalService(db.session_factory).decide(pending.value.approval_id, approved=True)
    await service.review(
        catalog["id"],
        ToolReview(
            expected_lock_version=1,
            tool_name="echo",
            approved=True,
            risk="R3",
            reviewer="another",
            reason="new approval contract",
        ),
    )
    assert (
        await executor.execute(call(adapter, "old", text="write"))
    ).error_code == "tool_manifest_changed"
    async with db.session_factory() as session:
        review = await session.scalar(
            select(MCPToolReviewRecord)
            .where(
                MCPToolReviewRecord.catalog_id == catalog["id"],
                MCPToolReviewRecord.tool_name == "echo",
            )
            .order_by(MCPToolReviewRecord.lock_version.desc())
            .limit(1)
        )
    binding = deepcopy(adapter.binding)
    binding["review_id"] = str(review.id)
    newer = MCPToolAdapter(binding, service, guard)
    fresh = ToolExecutor(
        ToolRegistry([newer]),
        InMemoryEventSink(aggregate.run.id),
        timeout_seconds=5,
        max_result_chars=1000,
        middleware=executor._middleware,
    )
    with pytest.raises(ApprovalRequiredError) as new_pending:
        await fresh.execute(call(newer, "new", text="write"))
    assert new_pending.value.approval_id != pending.value.approval_id
    assert PersistentToolMiddleware.semantic_key(
        adapter.name, {"text": "write"}
    ) == PersistentToolMiddleware.semantic_key(newer.name, {"text": "write"})
    assert not log.exists()


@pytest.mark.parametrize("fault", [{"is_error": True}, {"disconnect_after_call": True}])
async def test_write_uncertain_is_unknown_and_never_replayed(environment, fault):
    db, _, _, _, _ = environment
    _, _, _, _, _, adapter, executor, log = await setup_execution(environment, write=True, **fault)
    with pytest.raises(ApprovalRequiredError) as pending:
        await executor.execute(call(adapter, text="write"))
    await ApprovalService(db.session_factory).decide(pending.value.approval_id, approved=True)
    result = await executor.execute(call(adapter, "approved", text="write"))
    assert result.error_code is not None
    async with db.session_factory() as session:
        effect = await session.scalar(select(ToolEffectRecord))
        assert effect.status is ToolEffectStatus.UNKNOWN
    with pytest.raises(ApprovalRequiredError):
        await executor.execute(call(adapter, "repeat", text="write"))
    assert len(log.read_text().splitlines()) == 1


@pytest.mark.parametrize("state_name", ["draining", "disabled"])
async def test_unload_blocks_new_calls(environment, state_name):
    _, _, _, service, _ = environment
    server, _, _, _, _, adapter, executor, log = await setup_execution(environment)
    await service.set_execution(
        server["id"],
        ExecutionUpdate(expected_lock_version=0, expected_execution_version=1, state=state_name),
    )
    result = await executor.execute(call(adapter, text="no"))
    assert result.error_code == f"mcp_server_{state_name}"
    assert not log.exists()


async def test_inflight_drain_completes_and_closes_connection(environment):
    _, _, manager, service, _ = environment
    server, _, _, _, _, adapter, executor, log = await setup_execution(environment, call_delay=0.6)
    running = asyncio.create_task(executor.execute(call(adapter, text="slow")))
    for _ in range(100):
        if log.exists():
            break
        await asyncio.sleep(0.02)
    assert log.exists()
    await service.set_execution(
        server["id"],
        ExecutionUpdate(expected_lock_version=0, expected_execution_version=1, state="draining"),
    )
    assert (await running).error_code is None
    assert server["id"] not in manager.connections
    assert (await executor.execute(call(adapter, "blocked"))).error_code == "mcp_server_draining"


async def test_catalog_change_does_not_mutate_frozen_registry(environment):
    _, _, _, service, state = environment
    server, _, aggregate, guard, registry, adapter, executor, log = await setup_execution(
        environment
    )
    before = registry.manifest_hash()
    write_state(state, 2, name="different")
    await service.discover(server["id"])
    recovered = ToolRegistry()
    await register_run_tools(service, recovered, aggregate.run.id, guard)
    assert recovered.manifest_hash() == before
    assert (await executor.execute(call(adapter))).error_code == "tool_manifest_changed"
    assert not log.exists()


async def test_output_schema_and_unsupported_content(environment):
    _, _, _, _, _, adapter, _, _ = await setup_execution(environment)
    adapter.spec["output_schema"] = {"type": "object", "required": ["ok"]}
    with pytest.raises(MCPExecutionError, match="mcp_output_invalid"):
        adapter.normalize(types.CallToolResult(content=[], structuredContent={}))
    adapter.spec["output_schema"] = None
    with pytest.raises(MCPExecutionError, match="unsupported_mcp_content"):
        adapter.normalize(
            types.CallToolResult(
                content=[types.ImageContent(type="image", data="", mimeType="image/png")]
            )
        )


def test_stable_names_distinguish_truncated_names_and_servers():
    identity = uuid4()
    names = [mapped_name(identity, "a" * 100 + str(i)) for i in range(100)]
    assert len(set(names)) == 100 and all(len(name) <= 64 for name in names)
    assert mapped_name(identity, "echo") == mapped_name(identity, "echo")
    assert mapped_name(uuid4(), "echo") != mapped_name(identity, "echo")


async def test_readonly_disconnect_retries_at_most_once(environment):
    _, _, _, _, _, adapter, executor, log = await setup_execution(
        environment, disconnect_after_call=True
    )
    result = await executor.execute(call(adapter))
    assert result.error_code == "mcp_connection_closed"
    assert len(log.read_text().splitlines()) == 2


async def test_emergency_disable_during_write_leaves_unknown(environment):
    db, _, _, service, _ = environment
    server, _, _, _, _, adapter, executor, log = await setup_execution(
        environment, write=True, call_delay=5
    )
    with pytest.raises(ApprovalRequiredError) as pending:
        await executor.execute(call(adapter))
    await ApprovalService(db.session_factory).decide(pending.value.approval_id, approved=True)
    running = asyncio.create_task(executor.execute(call(adapter, "running")))
    for _ in range(100):
        if log.exists():
            break
        await asyncio.sleep(0.02)
    assert log.exists()
    await service.set_execution(
        server["id"],
        ExecutionUpdate(expected_lock_version=0, expected_execution_version=1, state="disabled"),
    )
    assert (await asyncio.wait_for(running, 2)).error_code is not None
    async with db.session_factory() as session:
        assert (await session.scalar(select(ToolEffectRecord))).status is ToolEffectStatus.UNKNOWN
    assert len(log.read_text().splitlines()) == 1


async def test_committed_write_reuses_one_effect(environment):
    db, _, _, _, _ = environment
    _, _, _, _, _, adapter, executor, log = await setup_execution(environment, write=True)
    with pytest.raises(ApprovalRequiredError) as pending:
        await executor.execute(call(adapter))
    await ApprovalService(db.session_factory).decide(pending.value.approval_id, approved=True)
    first = await executor.execute(call(adapter, "first"))
    second = await executor.execute(call(adapter, "second"))
    assert first.error_code is None and first.content == second.content
    assert len(log.read_text().splitlines()) == 1
    async with db.session_factory() as session:
        assert len(list(await session.scalars(select(ToolEffectRecord)))) == 1


@pytest.mark.parametrize("mode", ["baseline", "empty"])
async def test_eval_isolation_and_empty_snapshot_freeze(environment, mode):
    from evoagent.db.models import RunRecord

    db, _, _, service, _ = environment
    server, catalog, _, _, _, _, _, _ = await setup_execution(environment)
    if mode == "empty":
        await service.set_execution(
            server["id"],
            ExecutionUpdate(
                expected_lock_version=0, expected_execution_version=1, state="disabled"
            ),
        )
    tasks = TaskService(db.session_factory)
    session = await tasks.create_session("empty")
    aggregate = await tasks.create_task(
        session_id=session.id, goal="isolated", provider="mock", model="mock"
    )
    if mode != "empty":
        async with db.session_factory() as session:
            run = await session.get(RunRecord, aggregate.run.id)
            run.run_mode = mode
            await session.commit()
    lease = await JobLeaseManager(db.session_factory, lease_seconds=120).claim_next("next")
    guard = LeaseGuard(lease)
    registry = ToolRegistry()
    await register_run_tools(service, registry, aggregate.run.id, guard)
    assert len(registry) == 0
    if mode == "empty":
        await service.set_execution(
            server["id"],
            ExecutionUpdate(
                expected_lock_version=0,
                expected_execution_version=2,
                state="active",
                catalog_id=str(catalog["id"]),
            ),
        )
    await register_run_tools(service, registry, aggregate.run.id, guard)
    assert len(registry) == 0
    async with db.session_factory() as session:
        assert (await session.get(RunRecord, aggregate.run.id)).tool_catalog_snapshot == []


async def test_persistent_runner_exposes_and_executes_frozen_tools(environment):
    from evoagent.core.context import ContextBuilder
    from evoagent.core.models import FinishReason, Message, MessageRole, ModelResponse
    from evoagent.providers.mock import MockProvider
    from evoagent.runtime.persistent_runner import PersistentAgentRunner
    from evoagent.tasks.state_machine import PersistentRunStatus

    db, settings, _, _, _ = environment
    _, _, _, guard, registry, adapter, _, log = await setup_execution(environment)
    provider = MockProvider(
        [
            ModelResponse(
                message=Message(
                    role=MessageRole.ASSISTANT, tool_calls=(call(adapter, text="runner"),)
                ),
                finish_reason=FinishReason.TOOL_CALLS,
            ),
            ModelResponse(
                message=Message(role=MessageRole.ASSISTANT, content="done"),
                finish_reason=FinishReason.STOP,
            ),
        ]
    )
    runner = PersistentAgentRunner(
        settings=settings,
        session_factory=db.session_factory,
        context_builder=ContextBuilder(),
        provider=provider,
        registry=ToolRegistry(),
    )
    result = await runner.handle(guard.lease)
    assert result.status is PersistentRunStatus.COMPLETED
    assert provider.requests[0].tool_definitions == registry.definitions()
    assert json.loads(log.read_text())["arguments"] == {"text": "runner"}


async def test_execution_activation_cas(environment):
    _, _, _, service, _ = environment
    server, _, _, _, _, _, _, _ = await setup_execution(environment)
    with pytest.raises(MCPError, match="mcp_execution_conflict"):
        await service.set_execution(
            server["id"],
            ExecutionUpdate(
                expected_lock_version=0, expected_execution_version=0, state="disabled"
            ),
        )


async def test_revocation_between_remote_success_and_commit_is_unknown(environment):
    db, _, _, service, _ = environment
    server, _, _, _, _, adapter, executor, log = await setup_execution(environment, write=True)
    with pytest.raises(ApprovalRequiredError) as pending:
        await executor.execute(call(adapter))
    await ApprovalService(db.session_factory).decide(pending.value.approval_id, approved=True)

    class RevokeBeforeCommit:
        async def preserve(self, content, limit):
            await service.set_execution(
                server["id"],
                ExecutionUpdate(
                    expected_lock_version=0, expected_execution_version=1, state="disabled"
                ),
            )
            return content

    executor._output_store = RevokeBeforeCommit()
    with pytest.raises(MCPError, match="mcp_server_disabled"):
        await executor.execute(call(adapter, "remote-success"))
    async with db.session_factory() as session:
        assert (await session.scalar(select(ToolEffectRecord))).status is ToolEffectStatus.UNKNOWN
    assert len(log.read_text().splitlines()) == 1


async def test_stale_pending_approval_cannot_be_approved(environment):
    from evoagent.tools.approvals import ApprovalServiceError

    db, _, _, service, state = environment
    server, _, _, _, _, adapter, executor, log = await setup_execution(environment, write=True)
    with pytest.raises(ApprovalRequiredError) as pending:
        await executor.execute(call(adapter))
    write_state(state, 2, name="changed")
    await service.discover(server["id"])
    with pytest.raises(ApprovalServiceError, match="tool_manifest_changed"):
        await ApprovalService(db.session_factory).decide(pending.value.approval_id, approved=True)
    assert not log.exists()
