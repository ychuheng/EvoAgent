"""MCP 仅发现与本地审核入口，无 tools/call API。"""

from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Request
from sqlalchemy import select

from evoagent.api.dependencies import DatabaseDependency
from evoagent.db.models import (
    MCPCatalogRecord,
    MCPHealthRecord,
    MCPServerRecord,
    MCPToolReviewRecord,
)
from evoagent.mcp.schema import MCPError, MCPServerConfig, ServerUpdate, ToolReview
from evoagent.mcp.service import catalog_dto, review_dto, server_dto

router = APIRouter(prefix="/mcp", tags=["mcp"])


@router.post("/servers", status_code=201)
async def create_server(body: MCPServerConfig, request: Request):
    return await request.app.state.mcp_service.create(body)


@router.get("/servers")
async def list_servers(database: DatabaseDependency):
    async with database.session_factory() as session:
        return [server_dto(row) for row in await session.scalars(select(MCPServerRecord))]


@router.put("/servers/{server_id}")
async def update_server(server_id: UUID, body: ServerUpdate, request: Request):
    return await request.app.state.mcp_service.update(server_id, body)


@router.post("/servers/{server_id}/discover")
async def discover_server(server_id: UUID, request: Request):
    return await request.app.state.mcp_service.discover(server_id)


@router.post("/servers/{server_id}/disconnect", status_code=204)
async def disconnect_server(server_id: UUID, request: Request):
    await request.app.state.mcp_manager.disconnect(server_id)


@router.get("/servers/{server_id}/catalogs")
async def catalogs(server_id: UUID, database: DatabaseDependency):
    async with database.session_factory() as session:
        return [
            catalog_dto(row)
            for row in await session.scalars(
                select(MCPCatalogRecord)
                .where(MCPCatalogRecord.server_id == server_id)
                .order_by(MCPCatalogRecord.revision)
            )
        ]


@router.get("/servers/{server_id}/health")
async def health(server_id: UUID, database: DatabaseDependency):
    async with database.session_factory() as session:
        server = await session.get(MCPServerRecord, server_id)
        if server is None:
            raise MCPError("mcp_server_not_found")
        result = []
        for row in await session.scalars(
            select(MCPHealthRecord).where(MCPHealthRecord.server_id == server_id)
        ):
            expiry = (
                row.expires_at.replace(tzinfo=UTC)
                if row.expires_at.tzinfo is None
                else row.expires_at
            )
            fresh = expiry > datetime.now(UTC) and row.config_version == server.lock_version
            result.append(
                {
                    "instance_id": row.instance_id,
                    "state": row.state if fresh else "stale",
                    "config_version": row.config_version,
                    "error_code": row.error_code,
                    "observed_at": row.observed_at,
                    "expires_at": row.expires_at,
                }
            )
        return result


@router.get("/catalogs/{catalog_id}/reviews")
async def reviews(catalog_id: UUID, database: DatabaseDependency):
    async with database.session_factory() as session:
        return [
            review_dto(row)
            for row in await session.scalars(
                select(MCPToolReviewRecord)
                .where(MCPToolReviewRecord.catalog_id == catalog_id)
                .order_by(MCPToolReviewRecord.tool_name, MCPToolReviewRecord.lock_version)
            )
        ]


@router.post("/catalogs/{catalog_id}/reviews")
async def review(catalog_id: UUID, body: ToolReview, request: Request):
    return await request.app.state.mcp_service.review(catalog_id, body)
