"""官方传输 SDK；预设启动、最小秘密注入与实际连接 IP 绑定。"""

import ipaddress
import os
import re
from contextlib import AsyncExitStack, asynccontextmanager
from urllib.parse import urlsplit

import httpx
from mcp import StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client

from evoagent.mcp.schema import MCPError
from evoagent.tools.guards import URLGuard


def resolve_secret(settings, reference):
    if reference is None:
        return None
    variable = settings.mcp_secret_refs.get(reference, "")
    if not re.fullmatch(r"EVOAGENT_MCP_SECRET_[A-Z0-9_]+", variable):
        raise MCPError("mcp_secret_ref_invalid")
    value = os.environ.get(variable)
    if not value or len(value) > 4096 or "\r" in value or "\n" in value:
        raise MCPError("mcp_secret_unavailable")
    return value


class LimitedStream(httpx.AsyncByteStream):
    def __init__(self, stream):
        self.stream = stream

    async def __aiter__(self):
        total = 0
        async for chunk in self.stream:
            total += len(chunk)
            if total > 2 * 1024 * 1024:
                raise MCPError("mcp_response_too_large")
            yield chunk

    async def aclose(self):
        await self.stream.aclose()


class PinnedTransport(httpx.AsyncBaseTransport):
    def __init__(self, url, address, inner=None):
        self.url = httpx.URL(url)
        self.address = address
        self.inner = inner or httpx.AsyncHTTPTransport(retries=0)

    async def handle_async_request(self, request):
        if request.url != self.url:
            raise MCPError("mcp_endpoint_changed")
        # 连接固定 IP，同时保留 HTTP Host 和 TLS 的证书校验主机名。
        extensions = {**request.extensions, "sni_hostname": self.url.host}
        pinned = httpx.Request(
            request.method,
            request.url.copy_with(host=self.address),
            headers=request.headers,
            stream=request.stream,
            extensions=extensions,
        )
        response = await self.inner.handle_async_request(pinned)
        if 300 <= response.status_code < 400:
            await response.aclose()
            raise MCPError("mcp_redirect_forbidden")
        if response.headers.get("content-encoding", "identity") != "identity":
            await response.aclose()
            raise MCPError("mcp_content_encoding_unsupported")
        response.stream = LimitedStream(response.stream)
        return response

    async def aclose(self):
        await self.inner.aclose()


async def endpoint_address(profile):
    parts = urlsplit(profile.url)
    if profile.local_fixture:
        return parts.hostname
    try:
        addresses = (str(ipaddress.ip_address(parts.hostname)),)
    except ValueError:
        addresses = await URLGuard._resolve_addresses(parts.hostname, parts.port or 443)
    if not addresses or any(not ipaddress.ip_address(address).is_global for address in addresses):
        raise MCPError("mcp_endpoint_forbidden")
    return sorted(addresses)[0]


@asynccontextmanager
async def open_transport(config, settings, *, server_id=None, config_version=None, lease=None):
    secret = resolve_secret(settings, config.secret_ref)
    async with AsyncExitStack() as stack:
        if config.transport == "stdio":
            profile = settings.mcp_launch_profiles.get(config.launch_profile_id)
            if profile is None:
                raise MCPError("mcp_profile_not_found")
            if profile.sandbox_profile_id:
                from evoagent.sandbox.stdio import controller_stdio

                if server_id is None or config.secret_ref:
                    raise MCPError("mcp_sandbox_context_invalid")
                streams = await stack.enter_async_context(
                    controller_stdio(
                        settings, server_id, config_version, profile.sandbox_profile_id, lease
                    )
                )
                yield streams
                return
            # stderr 全部丢弃（上限 0 字节），避免第三方任意输出泄露环境秘密。
            errlog = stack.enter_context(open(os.devnull, "w", encoding="utf-8"))  # noqa: SIM115
            streams = await stack.enter_async_context(
                stdio_client(
                    StdioServerParameters(
                        command=profile.command,
                        args=list(profile.args),
                        cwd=profile.cwd,
                        env={"MCP_AUTH_TOKEN": secret} if secret else {},
                    ),
                    errlog=errlog,
                )
            )
            yield streams
        else:
            profile = settings.mcp_http_profiles.get(config.endpoint_profile_id)
            if profile is None:
                raise MCPError("mcp_profile_not_found")
            address = await endpoint_address(profile)
            client = await stack.enter_async_context(
                httpx.AsyncClient(
                    transport=PinnedTransport(profile.url, address),
                    trust_env=False,
                    follow_redirects=False,
                    timeout=config.call_timeout,
                    headers={
                        "Accept-Encoding": "identity",
                        **({"Authorization": f"Bearer {secret}"} if secret else {}),
                    },
                )
            )
            streams = await stack.enter_async_context(
                streamable_http_client(
                    profile.url,
                    http_client=client,
                )
            )
            yield streams[:2]
