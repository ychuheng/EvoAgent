"""配置 CAS、不可变目录/审核和带有效期的进程健康观察。"""

import json
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from evoagent.db.models import (
    MCPCatalogRecord,
    MCPHealthRecord,
    MCPServerRecord,
    MCPToolReviewRecord,
)
from evoagent.mcp.discovery import catalog_diff
from evoagent.mcp.schema import MCPError, MCPServerConfig
from evoagent.skills.canonical import content_hash


def server_dto(row):
    return {
        "id": row.id,
        "config": row.config,
        "lock_version": row.lock_version,
        "latest_revision": row.latest_revision,
        "execution_state": row.execution_state,
        "execution_version": row.execution_version,
        "active_catalog_id": row.active_catalog_id,
    }


def catalog_dto(row, server=None):
    return {
        "id": row.id,
        "server_id": row.server_id,
        "revision": row.revision,
        "config_version": row.config_version,
        "content_hash": row.content_hash,
        "protocol_version": row.protocol_version,
        "server_info": row.server_info,
        "capabilities": row.capabilities,
        "tools": row.tools,
        "diff": row.diff,
        "created_at": row.created_at,
        "execution_enabled": bool(
            server
            and server.execution_state == "active"
            and server.config.get("enabled")
            and server.active_catalog_id == row.id
            and server.lock_version == row.config_version
            and server.latest_revision == row.revision
        ),
    }


def review_dto(row):
    return {
        "tool_name": row.tool_name,
        "lock_version": row.lock_version,
        "approved": row.approved,
        "risk": row.risk,
        "effect": row.effect,
        "reviewer": row.reviewer,
        "reason": row.reason,
        "created_at": row.created_at,
    }


class MCPService:
    def __init__(self, factory, settings, manager):
        self.factory, self.settings, self.manager = factory, settings, manager

    def validate_config(self, config):
        profiles = (
            self.settings.mcp_launch_profiles
            if config.transport == "stdio"
            else self.settings.mcp_http_profiles
        )
        key = config.launch_profile_id or config.endpoint_profile_id
        if key not in profiles:
            raise MCPError("mcp_profile_not_found")
        if config.transport == "stdio" and profiles[key].sandbox_profile_id:
            spec = self.settings.sandbox_profiles.get(profiles[key].sandbox_profile_id)
            if spec is None or spec.mode != "stdio" or not spec.stdio_command or config.secret_ref:
                raise MCPError("mcp_sandbox_profile_invalid")
        if config.secret_ref and config.secret_ref not in self.settings.mcp_secret_refs:
            raise MCPError("mcp_secret_ref_invalid")

    async def create(self, config):
        self.validate_config(config)
        async with self.factory() as session:
            row = MCPServerRecord(config=config.model_dump(mode="json"))
            session.add(row)
            await session.commit()
            return server_dto(row)

    async def update(self, identity, request):
        self.validate_config(request.config)
        async with self.factory() as session:
            row = await session.scalar(
                select(MCPServerRecord).where(MCPServerRecord.id == identity).with_for_update()
            )
            if row is None:
                raise MCPError("mcp_server_not_found")
            if row.lock_version != request.expected_lock_version:
                raise MCPError("mcp_config_conflict")
            changed = await session.execute(
                update(MCPServerRecord)
                .where(
                    MCPServerRecord.id == identity,
                    MCPServerRecord.lock_version == request.expected_lock_version,
                )
                .values(
                    config=request.config.model_dump(mode="json"),
                    lock_version=request.expected_lock_version + 1,
                )
            )
            if changed.rowcount != 1:
                raise MCPError("mcp_config_conflict")
            await session.commit()
            result = server_dto(row)
        # 先提交新的配置版本；旧连接的任何迟到目录会被 publish 拒绝。
        await self.manager.disconnect(identity)
        return result

    async def discover(self, identity):
        async with self.factory() as session:
            row = await session.get(MCPServerRecord, identity)
            if row is None:
                raise MCPError("mcp_server_not_found")
            config, version = MCPServerConfig.model_validate(row.config), row.lock_version
        self.validate_config(config)

        async def publish(tools, initialization):
            return await self.publish(identity, version, tools, initialization)

        async def observe(state, error):
            await self.observe(identity, version, state, error)

        return await self.manager.discover(identity, version, config, publish, observe)

    async def publish(self, identity, version, tools, initialization):
        info = initialization.serverInfo.model_dump(mode="json", exclude_none=True)
        capabilities = initialization.capabilities.model_dump(mode="json", exclude_none=True)
        if len(json.dumps([info, capabilities]).encode()) > 16384:
            raise MCPError("mcp_initialization_limit")
        async with self.factory() as session:
            source = await session.get(MCPServerRecord, identity)
            launch = self.settings.mcp_launch_profiles.get(source.config.get("launch_profile_id"))
        profile_evidence = {}
        if launch and launch.sandbox_profile_id:
            spec = self.settings.sandbox_profiles[launch.sandbox_profile_id]
            profile_evidence = {"sandbox_profile_hash": content_hash(spec.model_dump(mode="json"))}
        digest = content_hash(
            {
                "tools": tools,
                "server_info": info,
                "capabilities": capabilities,
                "protocol": initialization.protocolVersion,
                **profile_evidence,
            }
        )
        async with self.factory() as session:
            server = await session.scalar(
                select(MCPServerRecord).where(MCPServerRecord.id == identity).with_for_update()
            )
            if server.lock_version != version or not server.config["enabled"]:
                raise MCPError("mcp_config_changed")
            previous = await session.scalar(
                select(MCPCatalogRecord).where(
                    MCPCatalogRecord.server_id == identity,
                    MCPCatalogRecord.revision == server.latest_revision,
                )
            )
            if previous and previous.content_hash == digest and previous.config_version == version:
                return catalog_dto(previous, server)
            next_revision = server.latest_revision + 1
            changed = await session.execute(
                update(MCPServerRecord)
                .where(
                    MCPServerRecord.id == identity,
                    MCPServerRecord.lock_version == version,
                    MCPServerRecord.latest_revision == next_revision - 1,
                )
                .values(latest_revision=next_revision)
            )
            if changed.rowcount != 1:
                raise MCPError("mcp_catalog_conflict")
            row = MCPCatalogRecord(
                server_id=identity,
                revision=next_revision,
                config_version=version,
                content_hash=digest,
                protocol_version=initialization.protocolVersion,
                server_info=info,
                capabilities=capabilities,
                tools=list(tools),
                diff=catalog_diff(previous.tools if previous else (), tools),
            )
            session.add(row)
            await session.flush()
            for tool in tools:
                session.add(MCPToolReviewRecord(catalog_id=row.id, tool_name=tool["name"]))
            await session.commit()
            return catalog_dto(row, server)

    async def observe(self, identity, version, state, error):
        async with self.factory() as session:
            server = await session.scalar(
                select(MCPServerRecord).where(MCPServerRecord.id == identity).with_for_update()
            )
            if server is None or server.lock_version != version:
                return
            row = await session.scalar(
                select(MCPHealthRecord).where(
                    MCPHealthRecord.server_id == identity,
                    MCPHealthRecord.instance_id == self.manager.instance_id,
                )
            )
            if row is None:
                row = MCPHealthRecord(server_id=identity, instance_id=self.manager.instance_id)
                session.add(row)
            row.config_version, row.state, row.error_code = version, state, error
            row.observed_at = datetime.now(UTC)
            row.expires_at = row.observed_at + timedelta(seconds=60)
            await session.commit()

    async def review(self, catalog_id, request):
        async with self.factory() as session:
            catalog = await session.get(MCPCatalogRecord, catalog_id)
            if catalog is None:
                raise MCPError("mcp_catalog_not_found")
            server = await session.scalar(
                select(MCPServerRecord)
                .where(MCPServerRecord.id == catalog.server_id)
                .with_for_update()
            )
            if (
                catalog.config_version != server.lock_version
                or catalog.revision != server.latest_revision
            ):
                raise MCPError("mcp_catalog_stale")
            fenced = await session.execute(
                update(MCPServerRecord)
                .where(
                    MCPServerRecord.id == server.id,
                    MCPServerRecord.lock_version == catalog.config_version,
                    MCPServerRecord.latest_revision == catalog.revision,
                )
                .values(latest_revision=catalog.revision)
            )
            if fenced.rowcount != 1:
                raise MCPError("mcp_catalog_stale")
            old = await session.scalar(
                select(MCPToolReviewRecord)
                .where(
                    MCPToolReviewRecord.catalog_id == catalog_id,
                    MCPToolReviewRecord.tool_name == request.tool_name,
                )
                .order_by(MCPToolReviewRecord.lock_version.desc())
                .limit(1)
            )
            if old is None:
                raise MCPError("mcp_tool_not_found")
            if old.lock_version != request.expected_lock_version:
                raise MCPError("mcp_review_conflict")
            row = MCPToolReviewRecord(
                catalog_id=catalog_id,
                lock_version=old.lock_version + 1,
                **request.model_dump(exclude={"expected_lock_version"}),
            )
            session.add(row)
            try:
                await session.commit()
            except IntegrityError:
                raise MCPError("mcp_review_conflict") from None
            return review_dto(row)

    async def set_execution(self, identity, request):
        async with self.factory() as session:
            server = await session.scalar(
                select(MCPServerRecord).where(MCPServerRecord.id == identity).with_for_update()
            )
            if server is None:
                raise MCPError("mcp_server_not_found")
            if server.lock_version != request.expected_lock_version:
                raise MCPError("mcp_config_conflict")
            catalog_id = server.active_catalog_id
            if request.state == "active":
                try:
                    catalog_id = UUID(request.catalog_id or "")
                except ValueError:
                    raise MCPError("mcp_catalog_not_found") from None
                catalog = await session.get(MCPCatalogRecord, catalog_id)
                if (
                    catalog is None
                    or catalog.server_id != identity
                    or catalog.config_version != server.lock_version
                    or catalog.revision != server.latest_revision
                    or not server.config.get("enabled")
                ):
                    raise MCPError("mcp_catalog_stale")
            # 激活状态独立于连接配置版本；切换 active catalog 不重写旧 Run 契约。
            changed = await session.execute(
                update(MCPServerRecord)
                .where(
                    MCPServerRecord.id == identity,
                    MCPServerRecord.lock_version == request.expected_lock_version,
                    MCPServerRecord.execution_version == request.expected_execution_version,
                )
                .values(
                    execution_state=request.state,
                    active_catalog_id=catalog_id,
                    execution_version=request.expected_execution_version + 1,
                )
            )
            if changed.rowcount != 1:
                raise MCPError("mcp_execution_conflict")
            await session.commit()
            result = server_dto(server)
        if request.state == "disabled":
            await self.manager.disconnect(identity)
        elif request.state == "draining":
            await self.manager.drain(identity)
        return result
