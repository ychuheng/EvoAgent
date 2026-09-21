"""完整分页后才产生候选目录；元数据不等于工具授权。"""

import json

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from mcp import types

from evoagent.mcp.schema import MCPError
from evoagent.skills.canonical import content_hash

MAX_TOOLS = 128
MAX_PAGES = 16
MAX_SCHEMA_BYTES = 65536
MAX_CATALOG_BYTES = 1048576


def check_schema(schema):
    if not isinstance(schema, dict) or schema.get("type") != "object":
        raise MCPError("mcp_schema_unsupported")
    if len(json.dumps(schema, ensure_ascii=False).encode()) > MAX_SCHEMA_BYTES:
        raise MCPError("mcp_schema_too_large")

    def walk(value, depth=0):
        if depth > 16:
            raise MCPError("mcp_schema_too_deep")
        if isinstance(value, dict):
            for key, item in value.items():
                if key in {"$ref", "$dynamicRef"} and (
                    not isinstance(item, str) or not item.startswith("#")
                ):
                    raise MCPError("mcp_remote_ref_forbidden")
                if key in {"$id", "$dynamicAnchor"}:
                    raise MCPError("mcp_schema_unsupported")
                if key == "$schema" and item != "https://json-schema.org/draft/2020-12/schema":
                    raise MCPError("mcp_schema_unsupported")
                walk(item, depth + 1)
        elif isinstance(value, list):
            for item in value:
                walk(item, depth + 1)

    walk(schema)
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError:
        raise MCPError("mcp_schema_invalid") from None


async def discover(session):
    tools, cursors, cursor, size = {}, set(), None, 0
    for _ in range(MAX_PAGES):
        page = await session.list_tools(params=types.PaginatedRequestParams(cursor=cursor))
        for tool in page.tools:
            if (
                not tool.name
                or len(tool.name) > 128
                or tool.name in tools
                or any(ord(char) < 32 for char in tool.name)
            ):
                raise MCPError("mcp_tool_name_invalid")
            if len(tool.description or "") > 4096:
                raise MCPError("mcp_description_too_long")
            check_schema(tool.inputSchema)
            if tool.outputSchema is not None:
                check_schema(tool.outputSchema)
            item = {
                "name": tool.name,
                "description": tool.description or "",
                "input_schema": tool.inputSchema,
                "output_schema": tool.outputSchema,
                "input_schema_hash": content_hash(tool.inputSchema),
                "output_schema_hash": content_hash(tool.outputSchema),
                "annotations": tool.annotations.model_dump(mode="json", exclude_none=True)
                if tool.annotations
                else {},
            }
            tools[tool.name] = item
            size += len(json.dumps(item, ensure_ascii=False).encode())
            if len(tools) > MAX_TOOLS or size > MAX_CATALOG_BYTES:
                raise MCPError("mcp_catalog_limit")
        cursor = page.nextCursor
        if cursor is None:
            return tuple(tools[name] for name in sorted(tools))
        if not cursor or len(cursor) > 2048 or cursor in cursors:
            raise MCPError("mcp_cursor_invalid")
        cursors.add(cursor)
    raise MCPError("mcp_page_limit")


def catalog_diff(previous, current):
    old = {tool["name"]: tool for tool in previous}
    new = {tool["name"]: tool for tool in current}
    return {
        "added": sorted(new.keys() - old.keys()),
        "removed": sorted(old.keys() - new.keys()),
        "changed": sorted(name for name in old.keys() & new.keys() if old[name] != new[name]),
    }
