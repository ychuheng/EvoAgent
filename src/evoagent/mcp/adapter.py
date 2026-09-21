"""冻结的 MCP 契约仅经 ToolExecutor / 持久化中间件执行。"""

import asyncio
import json
import re
from contextlib import suppress
from copy import deepcopy
from uuid import UUID

from jsonschema import Draft202012Validator
from pydantic import BaseModel, JsonValue
from sqlalchemy import select

from evoagent.core.models import ToolDefinition, ToolRisk
from evoagent.db.models import MCPCatalogRecord, MCPServerRecord, MCPToolReviewRecord, RunRecord
from evoagent.mcp.bindings import check_binding
from evoagent.mcp.discovery import check_schema
from evoagent.mcp.schema import MCPError
from evoagent.skills.canonical import content_hash
from evoagent.tools.base import BaseTool, ToolArgumentValidationError, ToolExecutionError


class MCPExecutionError(ToolExecutionError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


class ValidatedMCPArguments(BaseModel):
    payload: dict[str, JsonValue]


def mapped_name(server_id, original):
    slug = re.sub(r"[^a-zA-Z0-9_]", "_", original)[:29] or "tool"
    suffix = content_hash([str(server_id), original]).removeprefix("sha256:")[:16]
    return f"mcp_{UUID(str(server_id)).hex[:8]}_{slug}_{suffix}"


def validate_payload(schema, payload):
    # 无网络引用；限制实例深度及字节数，并拒绝递归 Schema（发现阶段）。
    try:
        encoded = json.dumps(payload, allow_nan=False)
        if len(encoded.encode()) > 1048576:
            raise ValueError

        def depth(value, level=0):
            if level > 32:
                raise ValueError
            if isinstance(value, dict):
                for item in value.values():
                    depth(item, level + 1)
            elif isinstance(value, list):
                for item in value:
                    depth(item, level + 1)

        depth(payload)
        Draft202012Validator(schema).validate(payload)
    except Exception:
        raise ToolArgumentValidationError("MCP JSON does not satisfy frozen schema") from None


class MCPToolAdapter(BaseTool[ValidatedMCPArguments]):
    arguments_model = ValidatedMCPArguments
    parallel_safe = False
    requires_persistent_execution = True
    implementation_version = "mcp-adapter-1"

    def __init__(self, binding, service, guard):
        self.binding = deepcopy(binding)
        self.service, self.guard = service, guard
        self.spec = self.binding["tool"]
        check_schema(self.spec["input_schema"])
        self.name = mapped_name(binding["server_id"], self.spec["name"])
        self.description = self.spec["description"] or f"MCP tool: {self.spec['name']}"
        self.risk = ToolRisk(binding["risk"])
        self.has_side_effects = binding["effect"] != "read_only"

    def definition(self):
        return ToolDefinition(
            name=self.name,
            description=self.description,
            parameters=deepcopy(self.spec["input_schema"]),
        )

    def execution_binding(self):
        return deepcopy(self.binding)

    def validate_arguments(self, arguments):
        payload = deepcopy(dict(arguments))
        validate_payload(self.spec["input_schema"], payload)
        return ValidatedMCPArguments(payload=payload)

    def canonical_arguments(self, arguments):
        return deepcopy(arguments.payload)

    async def preflight(self, *, in_flight=False):
        async with self.service.factory() as session:
            if self.guard is not None:
                await self.guard.check(session)
            try:
                server = await check_binding(session, self.binding, in_flight=in_flight)
            except MCPError as error:
                raise MCPExecutionError(error.code) from None
            if server.execution_state == "draining":
                connected = self.service.manager.connections.get(server.id)
                if connected:
                    connected[1].state = "draining"

    async def invoke(self, arguments):
        await self.preflight()
        identity = UUID(self.binding["server_id"])
        attempts = 1 if self.has_side_effects else 2
        for attempt in range(attempts):
            try:
                await self.service.discover(identity)
                connection = self.service.manager.connections[identity][1]

                async def operation(session, catalog, future):
                    if catalog["content_hash"] != self.binding["catalog_hash"]:
                        raise MCPError("tool_manifest_changed")
                    await self.preflight()
                    request = asyncio.create_task(
                        session.call_tool(self.spec["name"], self.canonical_arguments(arguments))
                    )
                    try:
                        while not request.done():
                            await asyncio.wait({request}, timeout=0.1)
                            if future.cancelled():
                                raise MCPError("mcp_call_cancelled")
                            await self.preflight(in_flight=True)
                        try:
                            return await request
                        except RuntimeError:
                            raise MCPError("mcp_output_invalid") from None
                    finally:
                        if not request.done():
                            request.cancel()
                        with suppress(asyncio.CancelledError, Exception):
                            await request

                result = await connection.call(operation)
                await self.preflight(in_flight=True)
                return self.normalize(result)
            except MCPError as error:
                if attempt + 1 == attempts or error.code not in {
                    "mcp_transport_failed",
                    "mcp_timeout",
                    "mcp_connection_closed",
                }:
                    raise MCPExecutionError(error.code) from None
                await self.service.manager.disconnect(identity)
        raise MCPExecutionError("mcp_transport_failed")

    def normalize(self, result):
        if result.isError:
            raise MCPExecutionError("mcp_tool_error")
        if any(item.type != "text" for item in result.content):
            raise MCPExecutionError("unsupported_mcp_content")
        if self.spec["output_schema"] is not None:
            try:
                validate_payload(self.spec["output_schema"], result.structuredContent)
            except ToolArgumentValidationError:
                raise MCPExecutionError("mcp_output_invalid") from None
        payload = {"text": [item.text for item in result.content]}
        if result.structuredContent is not None:
            payload["structuredContent"] = result.structuredContent
        encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False)
        if len(encoded.encode()) > 2097152:
            raise MCPExecutionError("mcp_result_too_large")
        return encoded


async def register_run_tools(service, registry, run_id, guard):
    """空选择也持久化；恢复永不重新挑目录；实验路径固定不接入。"""
    async with service.factory() as session:
        await guard.check(session)
        run = await session.get(RunRecord, run_id, with_for_update=True)
        snapshot = run.tool_catalog_snapshot
        if snapshot is None:
            snapshot = []
            if run.run_mode == "retrieval" and run.config_snapshot is None:
                servers = await session.scalars(
                    select(MCPServerRecord).where(MCPServerRecord.execution_state == "active")
                )
                for server in servers:
                    catalog = await session.get(MCPCatalogRecord, server.active_catalog_id)
                    if (
                        not server.config.get("enabled")
                        or catalog is None
                        or catalog.config_version != server.lock_version
                        or catalog.revision != server.latest_revision
                    ):
                        continue
                    for spec in catalog.tools:
                        review = await session.scalar(
                            select(MCPToolReviewRecord)
                            .where(
                                MCPToolReviewRecord.catalog_id == catalog.id,
                                MCPToolReviewRecord.tool_name == spec["name"],
                            )
                            .order_by(MCPToolReviewRecord.lock_version.desc())
                            .limit(1)
                        )
                        if review is not None and review.approved:
                            snapshot.append(
                                {
                                    "server_id": str(server.id),
                                    "catalog_id": str(catalog.id),
                                    "catalog_hash": catalog.content_hash,
                                    "revision": catalog.revision,
                                    "config_version": catalog.config_version,
                                    "review_id": str(review.id),
                                    "risk": review.risk,
                                    "effect": review.effect,
                                    "tool": spec,
                                }
                            )
            run.tool_catalog_snapshot = snapshot
            await session.commit()
    for binding in snapshot:
        registry.register(MCPToolAdapter(binding, service, guard))
