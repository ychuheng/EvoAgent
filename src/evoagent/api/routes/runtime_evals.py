"""运行时实验使用独立 DTO、路由及报告，不复用 Skill baseline。"""

from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict

from evoagent.evals.runtime import RuntimeExperimentService
from evoagent.evals.runtime_schema import RuntimeExperimentSpec

router = APIRouter(prefix="/runtime-experiments", tags=["runtime-evaluations"])


class CreateRuntimeExperiment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dataset_id: UUID
    spec: RuntimeExperimentSpec


@router.post("", status_code=201)
async def create(body: CreateRuntimeExperiment, request: Request):
    try:
        record = await RuntimeExperimentService(request.app.state.database.session_factory).create(
            body.dataset_id, body.spec
        )
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    return {"id": record.id, "spec_hash": record.spec_hash, "status": record.status}


@router.post("/{experiment_id}/release/{arm}")
async def release(experiment_id: UUID, arm: str, request: Request):
    try:
        await RuntimeExperimentService(request.app.state.database.session_factory).release(
            experiment_id, arm
        )
    except ValueError as error:
        raise HTTPException(409, str(error)) from error
    return {"released": arm}


@router.post("/{experiment_id}/collect")
async def collect(experiment_id: UUID, request: Request):
    try:
        report = await RuntimeExperimentService(request.app.state.database.session_factory).collect(
            experiment_id
        )
    except ValueError as error:
        raise HTTPException(409, str(error)) from error
    return {"state": "completed" if report else "pending", "report": report}
