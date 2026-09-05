from pathlib import Path

import httpx
import pytest
import respx

from evoagent.tools.base import ToolExecutionError, ToolPermissionError
from evoagent.tools.builtin.file_read import FileReadArguments, FileReadTool
from evoagent.tools.builtin.web_fetch import WebFetchArguments, WebFetchTool
from evoagent.tools.guards import URLGuard


async def public_resolver(host: str, port: int) -> tuple[str, ...]:
    return ("93.184.216.34",)


@pytest.mark.asyncio
async def test_file_read_reads_utf8_file_inside_workspace(tmp_path: Path) -> None:
    target = tmp_path / "notes.txt"
    target.write_text("你好，EvoAgent", encoding="utf-8")

    result = await FileReadTool(tmp_path).invoke(FileReadArguments(path="notes.txt"))

    assert result == "你好，EvoAgent"


@pytest.mark.asyncio
async def test_file_read_rejects_outside_and_oversized_files(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "secret.txt"
    outside.write_text("secret", encoding="utf-8")
    (workspace / "large.txt").write_text("12345", encoding="utf-8")
    tool = FileReadTool(workspace, max_bytes=4)

    with pytest.raises(ToolPermissionError):
        await tool.invoke(FileReadArguments(path="../secret.txt"))
    with pytest.raises(ToolExecutionError, match="byte limit"):
        await tool.invoke(FileReadArguments(path="large.txt"))


@pytest.mark.asyncio
@respx.mock
async def test_web_fetch_reads_public_text_response() -> None:
    respx.get("https://example.com/").mock(
        return_value=httpx.Response(
            200,
            text="hello web",
            headers={"content-type": "text/plain; charset=utf-8"},
        )
    )
    async with WebFetchTool(URLGuard(public_resolver), timeout_seconds=1) as tool:
        result = await tool.invoke(WebFetchArguments(url="https://example.com"))

    assert result == "hello web"


@pytest.mark.asyncio
@respx.mock
async def test_web_fetch_revalidates_redirect_and_blocks_private_target() -> None:
    first = respx.get("https://example.com/start").mock(
        return_value=httpx.Response(
            302,
            headers={"location": "http://127.0.0.1/admin"},
        )
    )
    async with WebFetchTool(URLGuard(public_resolver), timeout_seconds=1) as tool:
        with pytest.raises(ToolPermissionError):
            await tool.invoke(WebFetchArguments(url="https://example.com/start"))

    assert first.called


@pytest.mark.asyncio
@respx.mock
async def test_web_fetch_enforces_streamed_size_limit() -> None:
    respx.get("https://example.com/large").mock(
        return_value=httpx.Response(
            200,
            content=b"12345",
            headers={"content-type": "text/plain"},
        )
    )
    async with WebFetchTool(
        URLGuard(public_resolver),
        timeout_seconds=1,
        max_response_bytes=4,
    ) as tool:
        with pytest.raises(ToolExecutionError, match="size limit"):
            await tool.invoke(WebFetchArguments(url="https://example.com/large"))


@pytest.mark.asyncio
@respx.mock
async def test_web_fetch_rejects_binary_content() -> None:
    respx.get("https://example.com/image").mock(
        return_value=httpx.Response(
            200,
            content=b"image",
            headers={"content-type": "image/png"},
        )
    )
    async with WebFetchTool(URLGuard(public_resolver), timeout_seconds=1) as tool:
        with pytest.raises(ToolExecutionError, match="text type"):
            await tool.invoke(WebFetchArguments(url="https://example.com/image"))


@pytest.mark.asyncio
@respx.mock
async def test_web_fetch_converts_timeout_to_tool_error() -> None:
    respx.get("https://example.com/slow").mock(side_effect=httpx.ReadTimeout("slow"))
    async with WebFetchTool(URLGuard(public_resolver), timeout_seconds=1) as tool:
        with pytest.raises(ToolExecutionError, match="timed out"):
            await tool.invoke(WebFetchArguments(url="https://example.com/slow"))
