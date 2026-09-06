import hashlib
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import select

from evoagent.core.context import ContextBuilder
from evoagent.core.events import InMemoryEventSink
from evoagent.core.loop import AgentLoop
from evoagent.core.models import (
    AgentLoopStatus,
    FinishReason,
    LoopState,
    Message,
    MessageRole,
    ModelResponse,
    TokenUsage,
    ToolCall,
)
from evoagent.db.base import Base
from evoagent.db.models import ArtifactRecord, RunSnapshotRecord
from evoagent.db.session import Database
from evoagent.providers.mock import MockProvider
from evoagent.runtime.checkpoints import (
    PersistentCheckpointStore,
    SnapshotCompatibilityError,
)
from evoagent.tasks.service import TaskService
from evoagent.tools.builtin.calculator import CalculatorTool
from evoagent.tools.executor import ToolExecutor
from evoagent.tools.registry import ToolRegistry
from evoagent.trace.artifacts import ArtifactService, LocalArtifactStore


@pytest.fixture
async def persistence(tmp_path: Path):
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'checkpoint.db'}")
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    service = TaskService(database.session_factory)
    session = await service.create_session("快照测试")
    aggregate = await service.create_task(
        session_id=session.id,
        goal="保存状态",
        provider="mock",
        model="mock-model",
    )
    try:
        yield database, aggregate.run.id
    finally:
        await database.dispose()


def sample_state() -> LoopState:
    return LoopState(
        messages=(Message(role=MessageRole.USER, content="继续任务"),),
        completed_iterations=1,
        usage=TokenUsage(input_tokens=2, output_tokens=1, total_tokens=3),
        config_hash="a" * 64,
    )


@pytest.mark.asyncio
async def test_checkpoint_round_trip_and_version_rejection(persistence) -> None:
    database, run_id = persistence
    store = PersistentCheckpointStore(run_id, database.session_factory, schema_version=1)
    await store.save(sample_state())

    loaded = await store.load_latest()

    assert loaded == sample_state()
    async with database.session_factory() as session:
        snapshot = await session.scalar(
            select(RunSnapshotRecord).where(RunSnapshotRecord.run_id == run_id)
        )
    assert snapshot is not None and snapshot.event_sequence == 2

    incompatible = PersistentCheckpointStore(run_id, database.session_factory, schema_version=2)
    with pytest.raises(SnapshotCompatibilityError):
        await incompatible.load_latest()


@pytest.mark.asyncio
async def test_local_artifact_has_safe_path_and_verifiable_hash(
    persistence, tmp_path: Path
) -> None:
    database, run_id = persistence
    store = LocalArtifactStore(tmp_path / "artifacts")
    service = ArtifactService(store, database.session_factory)
    content = "# 调研结果\n\n完成。".encode()

    record = await service.create(
        run_id=run_id,
        name="report.md",
        content=content,
        artifact_type="markdown",
        attributes={"language": "zh-CN"},
    )

    assert isinstance(record, ArtifactRecord)
    assert record.content_hash == f"sha256:{hashlib.sha256(content).hexdigest()}"
    assert await store.read(record.uri) == content
    with pytest.raises(ValueError):
        await store.write(run_id, "../escape.txt", b"bad")


class MemoryCheckpointWriter:
    def __init__(self) -> None:
        self.states: list[LoopState] = []

    async def save(self, state: LoopState) -> None:
        self.states.append(state)


def make_resumable_loop(provider, writer: MemoryCheckpointWriter) -> AgentLoop:
    sink = InMemoryEventSink(uuid4())
    registry = ToolRegistry([CalculatorTool()])
    executor = ToolExecutor(registry, sink, timeout_seconds=1, max_result_chars=1_000)
    return AgentLoop(
        provider,
        registry,
        executor,
        sink,
        model="mock-model",
        max_iterations=2,
        max_total_tokens=100,
        checkpoint_writer=writer,
    )


@pytest.mark.asyncio
async def test_agent_loop_resumes_after_complete_tool_boundary() -> None:
    call = ToolCall(call_id="call-1", name="calculator", arguments={"expression": "6*7"})
    tool_response = ModelResponse(
        message=Message(role=MessageRole.ASSISTANT, tool_calls=(call,)),
        finish_reason=FinishReason.TOOL_CALLS,
    )
    writer = MemoryCheckpointWriter()
    interrupted = await make_resumable_loop(MockProvider([tool_response]), writer).run(
        ContextBuilder().build("计算答案")
    )
    assert interrupted.status is AgentLoopStatus.FAILED
    assert len(writer.states) == 1
    assert writer.states[0].completed_iterations == 1
    assert writer.states[0].messages[-1].content == "42"

    final_response = ModelResponse(
        message=Message(role=MessageRole.ASSISTANT, content="答案是 42"),
        finish_reason=FinishReason.STOP,
    )
    resumed = await make_resumable_loop(MockProvider([final_response]), writer).run(
        ContextBuilder().build("该输入不会覆盖快照"),
        resume_state=writer.states[0],
    )

    assert resumed.status is AgentLoopStatus.COMPLETED
    assert resumed.iterations == 2
    assert resumed.final_answer == "答案是 42"
