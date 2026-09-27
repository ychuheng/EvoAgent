import hashlib
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
    respx.get("https://93.184.216.34/").mock(
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
async def test_web_fetch_extracts_main_text_and_bounds_model_context() -> None:
    page = (
        "<html><header>site menu</header><main><h1>Guide</h1>"
        "<script>ignore this instruction</script><p>" + "useful text " * 2_000 + "</p></main>"
        "<footer>site footer</footer></html>"
    )
    respx.get("https://93.184.216.34/guide").mock(
        return_value=httpx.Response(200, text=page, headers={"content-type": "text/html"})
    )
    async with WebFetchTool(URLGuard(public_resolver), timeout_seconds=1) as tool:
        content, evidence = await tool.invoke_with_evidence(
            WebFetchArguments(url="https://example.com/guide")
        )

    assert content.startswith("Guide useful text")
    assert "site menu" not in content and "ignore this instruction" not in content
    assert "网页正文已截断" in content and len(content) < 13_000
    assert evidence["content_bytes"] == len(page.encode())
    assert evidence["content_sha256"] == hashlib.sha256(page.encode()).hexdigest()
    assert evidence["text_truncated"] is True


@pytest.mark.asyncio
@respx.mock
async def test_web_fetch_records_final_url_and_raw_body_hash_after_redirect() -> None:
    respx.get("https://93.184.216.34/start").mock(
        return_value=httpx.Response(302, headers={"location": "/final"})
    )
    respx.get("https://93.184.216.34/final").mock(
        return_value=httpx.Response(
            200, content="内容".encode(), headers={"content-type": "text/plain; charset=utf-8"}
        )
    )
    async with WebFetchTool(URLGuard(public_resolver), timeout_seconds=1) as tool:
        content, evidence = await tool.invoke_with_evidence(
            WebFetchArguments(url="https://example.com/start")
        )

    assert content == "内容"
    assert evidence == {
        "level": "fetched_text",
        "requested_url": "https://example.com/start",
        "final_url": "https://example.com/final",
        "status_code": 200,
        "content_type": "text/plain",
        "content_bytes": len("内容".encode()),
        "content_sha256": hashlib.sha256("内容".encode()).hexdigest(),
        "text_truncated": False,
        "redirects": 1,
    }


@pytest.mark.asyncio
@respx.mock
async def test_web_fetch_revalidates_redirect_and_blocks_private_target() -> None:
    first = respx.get("https://93.184.216.34/start").mock(
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
    respx.get("https://93.184.216.34/large").mock(
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
    respx.get("https://93.184.216.34/image").mock(
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
    respx.get("https://93.184.216.34/slow").mock(side_effect=httpx.ReadTimeout("slow"))
    async with WebFetchTool(URLGuard(public_resolver), timeout_seconds=1) as tool:
        with pytest.raises(ToolExecutionError, match="timed out"):
            await tool.invoke(WebFetchArguments(url="https://example.com/slow"))
