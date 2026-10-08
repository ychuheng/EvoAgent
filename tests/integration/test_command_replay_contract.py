"""命令重放与 UNKNOWN 的实际边界（改造方案 §10.4）。

被钉住的契约是**调用级身份**，不是参数级幂等：

- 同一 call_id 在恢复时命中同一条副作用账本项；
- Provider 重新生成 call_id 时，即使 argv/cwd 相同也算新调用；
- "跑测试 → 改文件 → 用同一命令复测"依赖的正是第二点（必须真的再跑一次）；
- 结果未知时转 UNKNOWN + 人工确认，不自动重试。

不在这里起真实子进程：`run_command` 在本机模式下需要登记项目、白名单与逐次审批，
那条链路由 `tests/unit/test_project_commands.py` 与 `test_project_command_tool.py`
覆盖。这里验证的是**身份与账本语义**，用与 `run_command` 同形的测试工具驱动。
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

from evoagent.core.events import InMemoryEventSink
from evoagent.core.models import ToolCall, ToolRisk
from evoagent.db.base import Base
from evoagent.db.models import (
    ApprovalStatus,
    ToolApprovalRecord,
    ToolCallRecord,
    ToolEffectRecord,
    ToolEffectStatus,
    TurnRecord,
)
from evoagent.db.session import Database
from evoagent.db.unit_of_work import UnitOfWork
from evoagent.runtime.recovery import RecoveryAction, RecoveryService
from evoagent.tasks.lease import JobLeaseManager
from evoagent.tasks.service import TaskService
from evoagent.tools.base import BaseTool
from evoagent.tools.builtin.project_command import ProjectCommandTool, RunCommandArguments
from evoagent.tools.effects import PersistentToolMiddleware
from evoagent.tools.executor import ToolExecutor
from evoagent.tools.policy import PermissionPolicy
from evoagent.tools.registry import ToolRegistry


class _CommandShapedTool(BaseTool[RunCommandArguments]):
    """与 `run_command` 同形的测试工具：相同参数模型与身份声明。"""

    name = "run_command_probe"
    description = "Command-shaped probe for replay contract"
    arguments_model = RunCommandArguments
    risk = ToolRisk.R1
    has_side_effects = True
    parallel_safe = False
    dedupe_by_arguments = False

    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []

    async def invoke(self, arguments: RunCommandArguments) -> str:
        self.calls.append(arguments.argv)
        return f"run-{len(self.calls)}"


def test_project_command_declares_call_level_identity() -> None:
    """`run_command` 自己声明的身份粒度必须与契约一致（不能只靠文档）。"""

    assert ProjectCommandTool.name == "run_command"
    assert ProjectCommandTool.dedupe_by_arguments is False
    assert ProjectCommandTool.has_side_effects is True
    assert ProjectCommandTool.parallel_safe is False


def test_semantic_key_groups_arguments_only_without_call_id() -> None:
    argv = ["python", "-m", "pytest", "-q"]

    # 无 call_id：参数级身份，同一语义调用得到同一个键
    assert PersistentToolMiddleware.semantic_key("run_command", {"argv": argv}) == (
        PersistentToolMiddleware.semantic_key("run_command", {"argv": list(argv)})
    )
    # 带 call_id：调用级身份，重新生成的 call_id 必须得到不同的键
    first = PersistentToolMiddleware.semantic_key("run_command", {"argv": argv}, call_id="call-1")
    second = PersistentToolMiddleware.semantic_key("run_command", {"argv": argv}, call_id="call-2")
    assert first != second
    # 复用同一 call_id 才是"同一调用重放"
    assert first == PersistentToolMiddleware.semantic_key(
        "run_command", {"argv": list(argv)}, call_id="call-1"
    )


@pytest.mark.asyncio
async def test_same_argv_with_new_call_id_executes_again(tmp_path: Path) -> None:
    """同命令复测必须真的再执行一次，且账本上是两条独立记录。

    与 `test_policy_effects_approvals.py` 中的
    `test_repeatable_effect_reexecutes_new_call_but_replays_same_call` 覆盖同一机制；
    这里用**命令形状**的参数模型再钉一遍，因为 §10.4 的结论正是围绕它。
    """

    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'replay.db'}")
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    service = TaskService(database.session_factory)
    session = await service.create_session("命令重放")
    aggregate = await service.create_task(
        session_id=session.id, goal="跑测试并复测", provider="mock", model="mock-model"
    )
    tool = _CommandShapedTool()
    executor = ToolExecutor(
        ToolRegistry([tool]),
        InMemoryEventSink(aggregate.run.id),
        timeout_seconds=2,
        max_result_chars=1_000,
        middleware=PersistentToolMiddleware(
            task_id=aggregate.task.id,
            run_id=aggregate.run.id,
            session_factory=database.session_factory,
            policy=PermissionPolicy(),
        ),
    )
    argv = {"argv": ["python", "-m", "pytest", "-q"], "cwd": "."}
    # 语义键按**校验后**的参数计算（含 allow_network 等默认值），与中间件一致
    normalized = RunCommandArguments.model_validate(argv).model_dump(mode="json")
    first = ToolCall(call_id="call-1", name=tool.name, arguments=argv)
    second = ToolCall(call_id="call-2", name=tool.name, arguments=dict(argv))

    assert (await executor.execute(first)).content == "run-1"
    # 同一 call_id：命中已提交账本，不再执行
    assert (await executor.execute(first)).content == "run-1"
    # 新 call_id：同 argv 也算新调用，必须再次执行
    assert (await executor.execute(second)).content == "run-2"
    assert tool.calls == [("python", "-m", "pytest", "-q")] * 2

    async with database.session_factory() as db_session:
        effects = tuple(await db_session.scalars(select(ToolEffectRecord)))
    assert len(effects) == 2
    assert {effect.semantic_key for effect in effects} == {
        PersistentToolMiddleware.semantic_key(tool.name, normalized, call_id="call-1"),
        PersistentToolMiddleware.semantic_key(tool.name, normalized, call_id="call-2"),
    }
    await database.dispose()


@pytest.mark.asyncio
async def test_unknown_command_effect_waits_for_confirmation(tmp_path: Path) -> None:
    """命令结果未知时转 UNKNOWN + 人工确认，且不会被新 call_id 冒充为同一件事。"""

    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'unknown.db'}")
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    service = TaskService(database.session_factory)
    session = await service.create_session("未知副作用")
    aggregate = await service.create_task(
        session_id=session.id, goal="执行命令", provider="mock", model="mock-model"
    )
    manager = JobLeaseManager(database.session_factory, lease_seconds=1)
    claimed_at = datetime.now(UTC)
    lease = await manager.claim_next("worker-command", now=claimed_at)
    assert lease is not None

    arguments = RunCommandArguments.model_validate({"argv": ["python", "-V"], "cwd": "."})
    key = PersistentToolMiddleware.semantic_key(
        "run_command", arguments.model_dump(mode="json"), call_id="call-1"
    )
    async with UnitOfWork(database.session_factory) as unit:
        turn = TurnRecord(run_id=aggregate.run.id, sequence=1, status="running")
        unit.session.add(turn)
        await unit.session.flush()
        call_record = ToolCallRecord(
            run_id=aggregate.run.id,
            turn_id=turn.id,
            provider_call_id="call-1",
            tool_name="run_command",
            arguments=arguments.model_dump(mode="json"),
            risk="R1",
        )
        unit.session.add(call_record)
        await unit.session.flush()
        unit.effects.add(
            ToolEffectRecord(
                tool_call_id=call_record.id,
                effect_scope=str(aggregate.task.id),
                semantic_key=key,
                status=ToolEffectStatus.EXECUTING,
            )
        )
        await unit.commit()

    assert await manager.recover_expired(now=claimed_at + timedelta(seconds=2)) == 1
    decision = await RecoveryService(database.session_factory, snapshot_schema_version=1).recover(
        aggregate.task.id
    )
    assert decision.action is RecoveryAction.WAITING_CONFIRMATION

    async with database.session_factory() as db_session:
        effect = await db_session.scalar(select(ToolEffectRecord))
        approval = await db_session.scalar(select(ToolApprovalRecord))
    assert effect is not None and effect.status is ToolEffectStatus.UNKNOWN
    assert approval is not None and approval.status is ApprovalStatus.PENDING
    # 新 call_id 的同 argv 调用是**另一件事**，不会命中这条待确认项
    assert key != PersistentToolMiddleware.semantic_key(
        "run_command", arguments.model_dump(mode="json"), call_id="call-2"
    )
    await database.dispose()
