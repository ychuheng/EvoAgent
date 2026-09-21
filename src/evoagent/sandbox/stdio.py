"""认证的控制通道承载容器 stdio；不是对外 MCP WebSocket 协议。"""

from contextlib import asynccontextmanager
from dataclasses import asdict

import anyio
from mcp import types
from mcp.shared.message import SessionMessage
from websockets.asyncio.client import connect

from evoagent.sandbox.client import controller_headers
from evoagent.skills.canonical import content_hash


@asynccontextmanager
async def controller_stdio(settings, server_id, config_version, profile, lease=None):
    headers = controller_headers(settings)
    url = (
        settings.sandbox_controller_url.rstrip("/")
        .replace("https://", "wss://", 1)
        .replace("http://", "ws://", 1)
    )
    spec = settings.sandbox_profiles[profile]
    request = {
        "server_id": str(server_id),
        "config_version": config_version,
        "profile_hash": content_hash(spec.model_dump(mode="json")),
    }
    if lease is not None:
        request["lease"] = {
            key: str(value) if key.endswith("_id") else value
            for key, value in asdict(lease).items()
            if key in {"task_id", "run_id", "owner", "epoch"}
        }
    async with connect(
        url + "/stdio",
        additional_headers=headers,
        proxy=None,
        max_size=1048576,
        max_queue=8,
        open_timeout=10,
    ) as channel:
        import json

        await channel.send(json.dumps(request))
        incoming, receive = anyio.create_memory_object_stream(0)
        send, outgoing = anyio.create_memory_object_stream(0)

        async def read():
            async with incoming:
                async for line in channel:
                    await incoming.send(
                        SessionMessage(types.JSONRPCMessage.model_validate_json(line))
                    )

        async def write():
            async with outgoing:
                async for item in outgoing:
                    await channel.send(
                        item.message.model_dump_json(by_alias=True, exclude_none=True)
                    )

        async with anyio.create_task_group() as group:
            group.start_soon(read)
            group.start_soon(write)
            try:
                yield receive, send
            finally:
                group.cancel_scope.cancel()
