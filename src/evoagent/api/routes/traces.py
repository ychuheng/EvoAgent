"""Run Trace 查询路由。"""

from uuid import UUID

from fastapi import APIRouter, Request

from evoagent.trace.service import RunTrace, TraceService

router = APIRouter(prefix="/runs", tags=["traces"])


@router.get("/{run_id}/trace", response_model=RunTrace)
async def get_trace(run_id: UUID, request: Request) -> RunTrace:
    return await TraceService(request.app.state.database.session_factory).get_run_trace(run_id)
