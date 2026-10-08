"""模型所见工具视图（改造方案 §2.3）：元数据必须真的到达模型。

关键观察点是 **Provider 实际收到的消息**，而不是 `ToolResult` 字段或日志——
"只给 ToolResult 加字段"明确不算验收。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import BaseModel

from evoagent.config import Settings
from evoagent.core.events import InMemoryEventSink
from evoagent.core.loop import AgentLoop
from evoagent.core.models import (
    AgentLoopStatus,
    FinishReason,
    Message,
    MessageRole,
    ModelResponse,
    ToolCall,
    ToolResult,
    ToolResultStatus,
    ToolRisk,
    ToolViewMetadata,
)
from evoagent.db.base import Base
from evoagent.db.session import Database
from evoagent.providers.mock import MockProvider
from evoagent.tasks.service import TaskService
from evoagent.tools.base import BaseTool, ToolExecutionError
from evoagent.tools.executor import ToolExecutor
from evoagent.tools.output_store import ToolOutputStore
from evoagent.tools.output_view import (
    VIEW_HINT,
    PreparedToolOutput,
    render_model_view,
    split_view,
    unknown_view,
)
from evoagent.tools.registry import ToolRegistry
from evoagent.trace.artifacts import ArtifactService, LocalArtifactStore

SECRET_BODY = "配置如下：api_key = sk-abcdefghijklmnopqrst\n其余内容正常。"
CLEAN_BODY = "本批次共 12 行，编号唯一性检查通过。\n"


class _Arguments(BaseModel):
    value: str = "x"


class _ScriptedTool(BaseTool[_Arguments]):
    name = "scripted"
    description = "Returns scripted content"
    arguments_model = _Arguments
    risk = ToolRisk.R0
    has_side_effects = False
    parallel_safe = True

    def __init__(self, body: str) -> None:
        self.body = body

    async def invoke(self, arguments: _Arguments) -> str:
        return self.body


async def _environment(tmp_path: Path, body: str, *, max_result_chars: int = 20_000):
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'view.db'}")
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    service = TaskService(database.session_factory)
    session = await service.create_session("视图")
    aggregate = await service.create_task(
        session_id=session.id, goal="读配置", provider="mock", model="mock-model"
    )
    artifacts = ArtifactService(
        LocalArtifactStore(tmp_path / "artifacts"), database.session_factory
    )
    output_store = ToolOutputStore(aggregate.run.id, artifacts, database.session_factory)
    executor = ToolExecutor(
        ToolRegistry([_ScriptedTool(body)]),
        InMemoryEventSink(aggregate.run.id),
        timeout_seconds=2,
        max_result_chars=max_result_chars,
        output_store=output_store,
    )
    return database, aggregate, executor


@pytest.mark.asyncio
async def test_short_output_carries_redaction_marker(tmp_path: Path) -> None:
    database, _aggregate, executor = await _environment(tmp_path, SECRET_BODY)

    result = await executor.execute(ToolCall(call_id="c1", name="scripted", arguments={}))

    metadata = result.view_metadata
    assert metadata is not None
    assert metadata.source_view == "redacted"
    assert metadata.redacted is True
    assert metadata.rule_categories == ("credential",)
    assert metadata.truncated is False
    assert "sk-abcdefghijklmnopqrst" not in result.content
    assert "[REDACTED]" in result.content
    await database.dispose()


@pytest.mark.asyncio
async def test_clean_short_output_is_marked_verbatim(tmp_path: Path) -> None:
    database, _aggregate, executor = await _environment(tmp_path, CLEAN_BODY)

    result = await executor.execute(ToolCall(call_id="c1", name="scripted", arguments={}))

    metadata = result.view_metadata
    assert metadata is not None and metadata.source_view == "verbatim"
    assert metadata.redacted is False and metadata.rule_categories == ()
    assert result.content == CLEAN_BODY
    await database.dispose()


@pytest.mark.asyncio
async def test_existing_placeholder_is_not_treated_as_a_secret(tmp_path: Path) -> None:
    """合法字面量 `[REDACTED]` 不是命中：没有新替换就不得声称脱敏过。"""

    body = "文档示例里写的是 [REDACTED]，这只是一个占位符。"
    database, _aggregate, executor = await _environment(tmp_path, body)

    result = await executor.execute(ToolCall(call_id="c1", name="scripted", arguments={}))

    metadata = result.view_metadata
    assert metadata is not None
    assert metadata.redacted is False and metadata.source_view == "verbatim"
    assert result.content == body
    await database.dispose()


@pytest.mark.asyncio
async def test_long_output_is_marked_truncated_and_keeps_reference_first_in_body(
    tmp_path: Path,
) -> None:
    body = "填充行\n" * 5_000 + SECRET_BODY
    database, _aggregate, executor = await _environment(tmp_path, body, max_result_chars=4_000)

    result = await executor.execute(ToolCall(call_id="c1", name="scripted", arguments={}))

    metadata, content = split_view(result.content)
    # 分层：ToolResult.content 是正文，头部由 loop 层的 render_model_view 加。
    # 因此这里必须没有头部，且正文首行就是归档引用——指纹归一化依赖这个约定。
    assert metadata is None
    assert result.view_metadata is not None
    assert result.view_metadata.truncated is True
    assert result.view_metadata.source_view == "redacted"
    reference = json.loads(content.splitlines()[0])
    assert reference["read_tool"] == "artifact_read"
    assert "sk-abcdefghijklmnopqrst" not in content
    await database.dispose()


def test_header_that_cannot_fit_the_budget_fails_closed() -> None:
    """预算连完整头部都放不下时明确失败，不剪提示、也不回退原文。"""

    result = ToolResult(
        tool_call_id="c1",
        name="scripted",
        status=ToolResultStatus.SUCCESS,
        content="body",
        view_metadata=ToolViewMetadata(
            redacted=True, rule_categories=("credential",), source_view="redacted"
        ),
    )

    with pytest.raises(ToolExecutionError) as error:
        render_model_view(result, max_chars=10)
    assert error.value.code == "view_header_budget_exceeded"


def test_redacted_view_renders_fixed_hint() -> None:
    result = ToolResult(
        tool_call_id="c1",
        name="scripted",
        status=ToolResultStatus.SUCCESS,
        content="body",
        view_metadata=ToolViewMetadata(
            redacted=True, rule_categories=("credential",), source_view="redacted"
        ),
    )

    rendered = render_model_view(result)
    metadata, body = split_view(rendered)
    assert metadata is not None and metadata.redacted is True
    assert body == "body"
    assert VIEW_HINT in rendered


def test_legacy_view_without_metadata_renders_as_unknown() -> None:
    result = ToolResult(
        tool_call_id="c1", name="scripted", status=ToolResultStatus.SUCCESS, content="body"
    )

    metadata, body = split_view(render_model_view(result))

    assert metadata is not None and metadata.source_view == "unknown"
    assert body == "body"


def test_fingerprint_normalises_archived_outputs_across_view_header() -> None:
    """指纹必须跳过视图头，否则同一份归档的不同预览会被判成"有新进展"。"""

    reference = {
        "artifact_id": "11111111-1111-1111-1111-111111111111",
        "hash": "sha256:" + "a" * 64,
        "total_chars": 10_000,
        "read_tool": "artifact_read",
    }

    def fingerprint(preview: str, digest: str) -> str:
        payload = dict(reference, hash=digest)
        prepared = PreparedToolOutput(
            content=json.dumps(payload) + "\n" + preview,
            view_metadata=ToolViewMetadata(redacted=False, truncated=True, source_view="verbatim"),
        )
        result = ToolResult(
            tool_call_id="c1",
            name="scripted",
            status=ToolResultStatus.SUCCESS,
            content=render_model_view(
                ToolResult(
                    tool_call_id="c1",
                    name="scripted",
                    status=ToolResultStatus.SUCCESS,
                    content=prepared.content,
                    view_metadata=prepared.view_metadata,
                )
            ),
            view_metadata=prepared.view_metadata,
        )
        call = ToolCall(call_id="c1", name="scripted", arguments={})
        return AgentLoop._tool_fingerprint((call,), (result,))

    digest = "sha256:" + "a" * 64
    assert fingerprint("前 40 个字符……", digest) == fingerprint("另一段预览文本……", digest)
    assert fingerprint("同一段预览", digest) != fingerprint("同一段预览", "sha256:" + "b" * 64)


@pytest.mark.asyncio
async def test_provider_request_body_contains_the_view_header(tmp_path: Path) -> None:
    """真实 Provider 请求体是验收观察点：模型必须真的收到元数据。"""

    database, _aggregate, executor = await _environment(tmp_path, SECRET_BODY)
    provider = MockProvider(
        [
            ModelResponse(
                message=Message(
                    role=MessageRole.ASSISTANT,
                    tool_calls=(ToolCall(call_id="call-1", name="scripted", arguments={}),),
                ),
                finish_reason=FinishReason.TOOL_CALLS,
            ),
            ModelResponse(
                message=Message(role=MessageRole.ASSISTANT, content="已读取"),
                finish_reason=FinishReason.STOP,
            ),
        ]
    )
    loop = AgentLoop(
        provider,
        ToolRegistry([_ScriptedTool(SECRET_BODY)]),
        executor,
        InMemoryEventSink(_aggregate.run.id),
        model="mock-model",
        max_iterations=4,
        max_total_tokens=10_000,
    )

    result = await loop.run(
        (Message(role=MessageRole.USER, content="读一下配置"),),
    )

    assert result.status is AgentLoopStatus.COMPLETED
    sent = provider.requests[1].messages[-1]
    assert sent.role is MessageRole.TOOL
    metadata, body = split_view(sent.content)
    assert metadata is not None
    assert metadata.source_view == "redacted" and metadata.rule_categories == ("credential",)
    assert VIEW_HINT in sent.content
    assert "sk-abcdefghijklmnopqrst" not in body
    await database.dispose()


@pytest.mark.asyncio
async def test_reused_result_is_projected_before_sending(tmp_path: Path) -> None:
    """复用/旧缓存结果没有元数据，必须在发送前补做当前策略检查。"""

    database, _aggregate, executor = await _environment(tmp_path, CLEAN_BODY)
    stale = ToolResult(
        tool_call_id="c1",
        name="scripted",
        status=ToolResultStatus.SUCCESS,
        content=SECRET_BODY,
    )
    projected = executor._project(  # noqa: SLF001 - 直接验证投影出口
        ToolCall(call_id="c1", name="scripted", arguments={}), stale
    )

    assert projected.view_metadata is not None
    assert projected.view_metadata.source_view == "redacted"
    assert "sk-abcdefghijklmnopqrst" not in projected.content
    # 归档回读无法证明是原文：即使没发生新替换也不得标 verbatim
    archive_read = executor._project(  # noqa: SLF001
        ToolCall(call_id="c2", name="artifact_read", arguments={}),
        ToolResult(
            tool_call_id="c2",
            name="artifact_read",
            status=ToolResultStatus.SUCCESS,
            content=CLEAN_BODY,
        ),
    )
    assert archive_read.view_metadata is not None
    assert archive_read.view_metadata.source_view == "unknown"
    await database.dispose()


def test_settings_and_unknown_view_helpers_agree_on_schema_version() -> None:
    """元数据 schema_version 与文档一致；unknown 视图不声称可信。"""

    assert Settings(_env_file=None).context_policy == "bounded"
    assert unknown_view().schema_version == 1
    assert unknown_view().source_view == "unknown"


@pytest.mark.asyncio
async def test_stale_metadata_result_is_reprojected_under_current_policy(tmp_path: Path) -> None:
    """元数据**存在**不等于当前策略已通过：旧策略版本的正文必须重新脱敏。

    复核复现：带 v1 `view_metadata` 的结果若直接放行，v2 才覆盖的 DSN 会原样进模型。
    """

    from evoagent.privacy.redaction import POLICY_VERSION

    database, _aggregate, executor = await _environment(tmp_path, CLEAN_BODY)
    stale = ToolResult(
        tool_call_id="c1",
        name="scripted",
        status=ToolResultStatus.SUCCESS,
        content="DATABASE_URL=postgres://user:secret-pw@db.internal/app",
        view_metadata=ToolViewMetadata(
            schema_version=1, redacted=False, source_view="verbatim", policy_version=1
        ),
    )

    projected = executor._project(  # noqa: SLF001 - 直接验证投影出口
        ToolCall(call_id="c1", name="scripted", arguments={}), stale
    )

    assert projected.view_metadata is not None
    assert projected.view_metadata.policy_version == POLICY_VERSION
    assert projected.view_metadata.source_view == "redacted"
    assert "secret-pw" not in projected.content
    await database.dispose()


@pytest.mark.asyncio
async def test_current_policy_metadata_passes_through_unchanged(tmp_path: Path) -> None:
    """已经是当前策略版本的结果不再重做投影（避免无谓的二次处理）。"""

    from evoagent.privacy.redaction import POLICY_VERSION

    database, _aggregate, executor = await _environment(tmp_path, CLEAN_BODY)
    current = ToolResult(
        tool_call_id="c1",
        name="scripted",
        status=ToolResultStatus.SUCCESS,
        content=CLEAN_BODY,
        view_metadata=ToolViewMetadata(
            redacted=False, source_view="verbatim", policy_version=POLICY_VERSION
        ),
    )

    projected = executor._project(  # noqa: SLF001
        ToolCall(call_id="c1", name="scripted", arguments={}), current
    )

    assert projected is current
    await database.dispose()
