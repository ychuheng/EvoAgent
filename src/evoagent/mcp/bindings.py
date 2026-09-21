"""执行前、审批决定与提交事务共享的目录撤销检查。"""

from uuid import UUID

from sqlalchemy import select

from evoagent.db.models import MCPServerRecord, MCPToolReviewRecord
from evoagent.mcp.schema import MCPError


async def check_binding(session, binding, *, in_flight=False, lock=False):
    server = await session.get(MCPServerRecord, UUID(binding["server_id"]), with_for_update=lock)
    if server is None or not server.config.get("enabled") or server.execution_state == "disabled":
        raise MCPError("mcp_server_disabled")
    if server.execution_state == "draining" and not in_flight:
        raise MCPError("mcp_server_draining")
    review = await session.scalar(
        select(MCPToolReviewRecord)
        .where(
            MCPToolReviewRecord.catalog_id == UUID(binding["catalog_id"]),
            MCPToolReviewRecord.tool_name == binding["tool"]["name"],
        )
        .order_by(MCPToolReviewRecord.lock_version.desc())
        .limit(1)
    )
    if (
        server.lock_version != binding["config_version"]
        or server.latest_revision != binding["revision"]
        or review is None
        or str(review.id) != binding["review_id"]
        or not review.approved
    ):
        raise MCPError("tool_manifest_changed")
    return server
