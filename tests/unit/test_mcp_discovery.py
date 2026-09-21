import asyncio
import json
from unittest.mock import AsyncMock

import httpx
import pytest
from mcp import types

from evoagent.config import Settings
from evoagent.mcp.discovery import check_schema, discover
from evoagent.mcp.schema import HTTPProfile, MCPError, MCPServerConfig, ToolReview
from evoagent.mcp.transports import PinnedTransport, endpoint_address, resolve_secret


def tool(name="echo", **changes):
    return types.Tool(name=name, inputSchema={"type": "object"}, **changes)


@pytest.mark.parametrize(
    "schema,code",
    [
        ({"type": "string"}, "unsupported"),
        ({"type": "object", "$ref": "https://example.com/schema"}, "remote_ref"),
        ({"type": "object", "properties": {"x": {"type": "imaginary"}}}, "invalid"),
        ({"type": "object", "$schema": "https://unsupported.example/schema"}, "unsupported"),
        ({"type": "object", "$id": "https://example.com/"}, "unsupported"),
        ({"type": "object", "description": "x" * 65536}, "too_large"),
    ],
)
def test_schema_limits(schema, code):
    with pytest.raises(MCPError, match=code):
        check_schema(schema)


async def test_complete_pagination_and_order():
    session = AsyncMock()
    session.list_tools.side_effect = [
        types.ListToolsResult(tools=[tool("z")], nextCursor="next"),
        types.ListToolsResult(tools=[tool("a")]),
    ]
    result = await discover(session)
    assert [row["name"] for row in result] == ["a", "z"]
    assert session.list_tools.call_args.kwargs["params"].cursor == "next"
    assert result[0]["input_schema_hash"].startswith("sha256:")


@pytest.mark.parametrize("mode", ["duplicate", "cursor", "pages", "description", "count", "cancel"])
async def test_bad_or_unbounded_catalog_rejected(mode):
    session = AsyncMock()
    if mode == "duplicate":
        session.list_tools.return_value = types.ListToolsResult(tools=[tool(), tool()])
    elif mode == "cursor":
        session.list_tools.return_value = types.ListToolsResult(tools=[], nextCursor="loop")
    elif mode == "pages":
        session.list_tools.side_effect = [
            types.ListToolsResult(tools=[], nextCursor=str(n)) for n in range(20)
        ]
    elif mode == "description":
        session.list_tools.return_value = types.ListToolsResult(
            tools=[tool(description="x" * 4097)]
        )
    elif mode == "count":
        session.list_tools.return_value = types.ListToolsResult(
            tools=[tool(str(n)) for n in range(129)]
        )
    else:
        session.list_tools.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError if mode == "cancel" else MCPError):
        await discover(session)


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com/mcp",
        "https://user:secret@example.com/mcp",
        "https://example.com/mcp?key=secret",
    ],
)
def test_endpoint_configuration_rejects_credentials_and_insecure_remote(url):
    with pytest.raises(ValueError):
        HTTPProfile(url=url)


async def test_public_profile_rejects_private_address_and_fixture_exception_is_narrow():
    with pytest.raises(MCPError, match="endpoint_forbidden"):
        await endpoint_address(HTTPProfile(url="https://127.0.0.1/mcp"))
    with pytest.raises(ValueError):
        HTTPProfile(url="http://10.0.0.1/mcp", local_fixture=True)
    assert (
        await endpoint_address(HTTPProfile(url="http://127.0.0.1:1234/mcp", local_fixture=True))
        == "127.0.0.1"
    )


async def test_http_transport_pins_target_and_rejects_redirects():
    async def handle(request):
        assert request.url.host == "93.184.216.34"
        assert request.headers["host"] == "example.com"
        assert request.extensions["sni_hostname"] == "example.com"
        return httpx.Response(302, headers={"location": "http://127.0.0.1/private"})

    transport = PinnedTransport(
        "https://example.com/mcp", "93.184.216.34", httpx.MockTransport(handle)
    )
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(MCPError, match="redirect_forbidden"):
            await client.get("https://example.com/mcp")
        with pytest.raises(MCPError, match="endpoint_changed"):
            await client.get("https://example.com/other")


def test_secret_reference_and_deny_by_default_review(monkeypatch):
    settings = Settings(mcp_secret_refs={"fixture": "EVOAGENT_MCP_SECRET_FIXTURE"})
    monkeypatch.setenv("EVOAGENT_MCP_SECRET_FIXTURE", "private-token")
    assert resolve_secret(settings, "fixture") == "private-token"
    assert "private-token" not in settings.model_dump_json()
    with pytest.raises(MCPError, match="secret_ref_invalid"):
        resolve_secret(settings, "unknown")
    review = ToolReview(
        expected_lock_version=0, tool_name="echo", reviewer="local", reason="pending"
    )
    assert not review.approved and review.risk == "R3"
    with pytest.raises(ValueError):
        ToolReview(
            expected_lock_version=0, tool_name="echo", reviewer="local", reason="bad", risk="R0"
        )
    config = MCPServerConfig(name="fixture", transport="stdio", launch_profile_id="fixture")
    assert not config.enabled
    assert "private-token" not in json.dumps(config.model_dump())
