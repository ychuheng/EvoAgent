import json
import sys
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from ddgs.exceptions import DDGSException, RatelimitException, TimeoutException

from evoagent.core.models import ToolRisk
from evoagent.tools.base import ToolPermissionError
from evoagent.tools.builtin.file_write import FileWriteArguments, FileWriteTool
from evoagent.tools.builtin.shell import ShellArguments, ShellTool
from evoagent.tools.builtin.web_search import (
    BraveSearchProvider,
    DDGSSearchProvider,
    MockSearchProvider,
    SearchResult,
    WebSearchArguments,
    WebSearchError,
    WebSearchTool,
)
from evoagent.tools.sandbox import RunSandbox, ShellSandbox


@pytest.mark.asyncio
async def test_file_write_is_atomic_and_confined_to_run_directory(tmp_path: Path) -> None:
    tool = FileWriteTool(RunSandbox(tmp_path, uuid4()))

    relative = await tool.invoke(FileWriteArguments(path="reports/result.md", content="完成"))

    assert relative == "reports/result.md"
    assert (
        tool.effective_risk(FileWriteArguments(path="result.md", content="x", overwrite=True))
        is ToolRisk.R2
    )
    with pytest.raises(ToolPermissionError):
        await tool.invoke(FileWriteArguments(path="../escape.txt", content="bad"))


@pytest.mark.asyncio
async def test_mock_search_provider_makes_search_deterministic() -> None:
    result = SearchResult(
        title="EvoAgent",
        url="https://example.com/evoagent",
        snippet="测试结果",
        source="mock",
    )
    tool = WebSearchTool(MockSearchProvider([result]))

    payload = json.loads(await tool.invoke(WebSearchArguments(query="agent")))

    assert payload == [result.model_dump(mode="json")]

    content, evidence = await tool.invoke_with_evidence(WebSearchArguments(query="agent"))
    assert json.loads(content) == payload
    assert evidence == {
        "level": "search_snippet",
        "query": "agent",
        "provider": "mock",
        "results": payload,
    }


@pytest.mark.asyncio
async def test_zero_search_results_still_record_query_and_provider() -> None:
    tool = WebSearchTool(MockSearchProvider([]))

    content, evidence = await tool.invoke_with_evidence(WebSearchArguments(query="missing"))

    assert content == "[]"
    assert evidence == {
        "level": "search_snippet",
        "query": "missing",
        "provider": "mock",
        "results": [],
    }


@pytest.mark.asyncio
async def test_ddgs_search_is_keyless_and_keeps_real_source() -> None:
    calls: list[tuple[str, int]] = []

    def search(query: str, count: int) -> list[dict]:
        calls.append((query, count))
        return [{"title": "Guide", "href": "https://example.org/guide", "body": "Summary"}]

    tool = WebSearchTool(DDGSSearchProvider(search=search))
    content, evidence = await tool.invoke_with_evidence(WebSearchArguments(query="guide", count=2))

    assert calls == [("guide", 2)]
    assert json.loads(content) == [
        {
            "title": "Guide",
            "url": "https://example.org/guide",
            "snippet": "Summary",
            "source": "ddgs",
        }
    ]
    assert evidence["provider"] == "ddgs"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "code"),
    [
        (RatelimitException("limited"), "search_rate_limited"),
        (TimeoutException("late"), "search_timeout"),
        (DDGSException("backend unavailable"), "search_service_unavailable"),
    ],
)
async def test_ddgs_search_classifies_backend_failures(error: DDGSException, code: str) -> None:
    def fail(_query: str, _count: int) -> list[dict]:
        raise error

    with pytest.raises(WebSearchError) as failure:
        await DDGSSearchProvider(search=fail).search("query", count=3)
    assert failure.value.code == code
    assert "backend unavailable" not in str(failure.value)


@pytest.mark.asyncio
async def test_ddgs_search_rejects_credential_url_and_allows_zero_results() -> None:
    assert await DDGSSearchProvider(search=lambda _q, _n: []).search("none", count=3) == ()
    provider = DDGSSearchProvider(
        search=lambda _q, _n: [{"href": "https://user:token@example.org/"}]
    )
    with pytest.raises(WebSearchError) as failure:
        await provider.search("query", count=3)
    assert failure.value.code == "search_invalid_response"


@pytest.mark.asyncio
async def test_brave_search_keeps_source_and_allows_zero_results() -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.params["q"] == "none":
            return httpx.Response(200, json={"web": {"results": []}})
        return httpx.Response(
            200,
            json={
                "web": {
                    "results": [
                        {
                            "title": "Project",
                            "url": "https://example.com/",
                            "description": "Summary",
                        },
                    ]
                }
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        provider = BraveSearchProvider("private-token", client=client)
        assert await provider.search("none", count=5) == ()
        result = await provider.search("project", count=1)
        assert result == (
            SearchResult(
                title="Project",
                url="https://example.com/",
                snippet="Summary",
                source="brave",
            ),
        )
        assert requests[-1].headers["X-Subscription-Token"] == "private-token"
        assert requests[-1].url.params["count"] == "1"
        await provider.aclose()
        assert not client.is_closed


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status_code", "code"),
    [
        (401, "search_auth_failed"),
        (403, "search_forbidden"),
        (429, "search_rate_limited"),
        (500, "search_service_unavailable"),
        (400, "search_http_error"),
    ],
)
async def test_brave_search_classifies_http_failures_without_response_body(
    status_code: int,
    code: str,
) -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(status_code, text="private-token")
        )
    ) as client:
        with pytest.raises(WebSearchError) as failure:
            await BraveSearchProvider("private-token", client=client).search("query", count=5)
    assert failure.value.code == code
    assert "private-token" not in str(failure.value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        "not-json",
        "[]",
        "{}",
        '{"web":{"results":{}}}',
        '{"web":{"results":[{"url":"file:///private"}]}}',
        '{"web":{"results":[{"url":"https://user:secret@example.com"}]}}',
        '{"web":{"results":[{"title":"missing URL"}]}}',
    ],
)
async def test_brave_search_rejects_invalid_response(body: str) -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, text=body))
    ) as client:
        with pytest.raises(WebSearchError) as failure:
            await BraveSearchProvider("token", client=client).search("query", count=5)
    assert failure.value.code == "search_invalid_response"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("exception", "code"),
    [
        (httpx.ConnectTimeout("late"), "search_timeout"),
        (httpx.ConnectError("offline"), "search_network_error"),
    ],
)
async def test_brave_search_classifies_transport_failures(
    exception: httpx.RequestError,
    code: str,
) -> None:
    def fail(_request: httpx.Request) -> httpx.Response:
        raise exception

    async with httpx.AsyncClient(transport=httpx.MockTransport(fail)) as client:
        with pytest.raises(WebSearchError) as failure:
            await BraveSearchProvider("token", client=client).search("query", count=5)
    assert failure.value.code == code


@pytest.mark.asyncio
async def test_shell_uses_argv_allowlist_and_restricted_workspace(tmp_path: Path) -> None:
    sandbox = ShellSandbox(
        tmp_path,
        allowed_executables=(sys.executable,),
        timeout_seconds=5,
    )
    tool = ShellTool(sandbox)
    result = json.loads(
        await tool.invoke(ShellArguments(argv=(sys.executable, "-c", "print('sandbox-ok')")))
    )

    assert result["stdout"].strip() == "sandbox-ok"
    with pytest.raises(ToolPermissionError):
        await sandbox.run(("not-allowed", "--version"))
