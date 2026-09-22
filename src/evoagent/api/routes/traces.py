"""Run Trace 查询路由。"""

from uuid import UUID

from fastapi import APIRouter, Request
from sqlalchemy import select

from evoagent.api.dependencies import DatabaseDependency
from evoagent.db.models import ContextRevisionRecord, RunRecord
from evoagent.memory.schema import MemoryError
from evoagent.trace.service import RunTrace, TraceService

router = APIRouter(prefix="/runs", tags=["traces"])


@router.get("/{run_id}/trace", response_model=RunTrace)
async def get_trace(run_id: UUID, request: Request) -> RunTrace:
    return await TraceService(request.app.state.database.session_factory).get_run_trace(run_id)


@router.get("/{run_id}/context")
async def get_context(run_id: UUID, database: DatabaseDependency):
    """只返回冻结预算和修订证据，不展开消息、凭据或 Artifact 内容。"""
    async with database.session_factory() as session:
        run = await session.get(RunRecord, run_id)
        if run is None:
            raise MemoryError("run_not_found")
        config = run.config_snapshot or {}
        policy = config.get("context_policy") or {}
        revisions = await session.scalars(
            select(ContextRevisionRecord)
            .where(ContextRevisionRecord.run_id == run_id)
            .order_by(ContextRevisionRecord.revision)
        )
        return {
            "run_id": run.id,
            "status": run.status,
            "error_code": run.error_code,
            "config_hash": run.config_hash,
            "policy": {
                key: policy[key]
                for key in ("mode", "version", "budget", "counter", "strict")
                if key in policy
            },
            "max_output_tokens": config.get("max_output_tokens"),
            "revisions": [
                {
                    "id": row.id,
                    "revision": row.revision,
                    "parent_id": row.parent_id,
                    "input_hash": row.input_hash,
                    "policy_hash": row.policy_hash,
                    "artifact_id": row.artifact_id,
                    "estimate": row.estimate,
                    "created_at": row.created_at,
                }
                for row in revisions
            ],
        }
