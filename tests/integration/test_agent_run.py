from pathlib import Path

import httpx
import pytest
import respx

from evoagent.config import Settings
from evoagent.core.context import ContextBuilder
from evoagent.core.models import (
    FinishReason,
    Message,
    MessageRole,
    ModelResponse,
    RunStatus,
    ToolCall,
)
from evoagent.core.runner import AgentRunner
from evoagent.providers.mock import MockProvider
from evoagent.tools.builtin.calculator import CalculatorTool
from evoagent.tools.builtin.file_read import FileReadTool
from evoagent.tools.builtin.web_fetch import WebFetchTool
from evoagent.tools.guards import URLGuard
from evoagent.tools.registry import ToolRegistry


async def public_resolver(host: str, port: int) -> tuple[str, ...]:
    return ("93.184.216.34",)


@pytest.mark.asyncio
@respx.mock
async def test_complete_run_uses_multiple_tools_and_returns_answer(tmp_path: Path) -> None:
    (tmp_path / "notes.txt").write_text("local evidence", encoding="utf-8")
    respx.get("https://example.com/source").mock(
        return_value=httpx.Response(
            200,
            text="web evidence",
            headers={"content-type": "text/plain"},
        )
    )
    calls = (
        ToolCall(
            call_id="calculate",
            name="calculator",
            arguments={"expression": "6 * 7"},
        ),
        ToolCall(
            call_id="read",
            name="file_read",
            arguments={"path": "notes.txt"},
        ),
        ToolCall(
            call_id="fetch",
            name="web_fetch",
            arguments={"url": "https://example.com/source"},
        ),
    )
    provider = MockProvider(
        [
            ModelResponse(
                message=Message(role=MessageRole.ASSISTANT, tool_calls=calls),
                finish_reason=FinishReason.TOOL_CALLS,
            ),
            ModelResponse(
                message=Message(
                    role=MessageRole.ASSISTANT,
                    content="calculation=42; local and web evidence collected",
                ),
                finish_reason=FinishReason.STOP,
            ),
        ]
    )
    web_fetch = WebFetchTool(URLGuard(public_resolver), timeout_seconds=1)
    registry = ToolRegistry([CalculatorTool(), FileReadTool(tmp_path), web_fetch])
    runner = AgentRunner(
        Settings(_env_file=None, workspace=tmp_path),
        ContextBuilder(),
        provider,
        registry,
    )

    try:
        result = await runner.run("collect evidence")
    finally:
        await web_fetch.aclose()

    assert result.status is RunStatus.COMPLETED
    assert result.final_answer == "calculation=42; local and web evidence collected"
    tool_messages = provider.requests[1].messages[-3:]
    assert [message.tool_call_id for message in tool_messages] == [
        "calculate",
        "read",
        "fetch",
    ]
    assert [message.content for message in tool_messages] == [
        "42",
        "local evidence",
        "web evidence",
    ]
