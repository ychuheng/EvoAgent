"""受信任的本地协议 fixture，提供目录、回显与可控故障注入。"""

import argparse
import asyncio
import json
import os
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from mcp import types
from mcp.server.lowlevel import NotificationOptions, Server
from mcp.server.stdio import stdio_server


def build_server(state_path=None):
    server = Server("evoagent-local-fixture", version="1")
    state = {"session": None, "revision": None}

    def read():
        if state_path:
            return json.loads(Path(state_path).read_text(encoding="utf-8"))
        return {
            "revision": 1,
            "tools": [
                {
                    "name": "fixture_echo",
                    "description": "受信 fixture 的文本示例",
                    "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}},
                },
                {
                    "name": "fixture_health",
                    "inputSchema": {"type": "object"},
                    "annotations": {"readOnlyHint": True},
                },
            ],
        }

    @server.list_tools()
    async def list_tools(request: types.ListToolsRequest):
        data = read()
        if data.get("delay"):
            await asyncio.sleep(data["delay"])
        state["session"] = server.request_context.session
        cursor = int(request.params.cursor) if request.params and request.params.cursor else 0
        rows = data["tools"]
        page = rows[cursor : cursor + 1]
        next_cursor = str(cursor + 1) if cursor + 1 < len(rows) else None
        return types.ListToolsResult(
            tools=[types.Tool.model_validate(tool) for tool in page], nextCursor=next_cursor
        )

    @server.call_tool(validate_input=False)
    async def call_tool(name, arguments):
        data = read()
        if data.get("call_log"):
            with Path(data["call_log"]).open("a", encoding="utf-8") as stream:
                stream.write(json.dumps({"name": name, "arguments": arguments}) + "\n")
        if data.get("call_delay"):
            await asyncio.sleep(data["call_delay"])
        if data.get("disconnect_after_call"):
            os._exit(23)
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=arguments.get("text", "ok"))],
            structuredContent=data.get("structured_content"),
            isError=data.get("is_error", False),
        )

    async def watch():
        while True:
            data = read()
            revision = data["revision"]
            if state["revision"] is not None and state["revision"] != revision and state["session"]:
                await state["session"].send_tool_list_changed()
            state["revision"] = revision
            await asyncio.sleep(0.05)

    @asynccontextmanager
    async def lifespan(_server):
        task = asyncio.create_task(watch())
        try:
            yield {}
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    server.lifespan = lifespan
    return server


async def serve_stdio(state_path):
    server = build_server(state_path)
    async with stdio_server() as streams:
        await server.run(
            *streams,
            server.create_initialization_options(
                notification_options=NotificationOptions(tools_changed=True)
            ),
        )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-file")
    parser.add_argument("--pid-file")
    args = parser.parse_args()
    if args.pid_file:
        Path(args.pid_file).write_text(str(os.getpid()), encoding="ascii")
    asyncio.run(serve_stdio(args.state_file))


if __name__ == "__main__":
    main()
