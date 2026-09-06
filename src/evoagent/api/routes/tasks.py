"""Task HTTP 路由。"""

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, status

from evoagent.api.dependencies import SettingsDependency, TaskServiceDependency
from evoagent.api.schemas import RunResponse, TaskCreateRequest, TaskResponse
from evoagent.tasks.service import TaskAggregate

router = APIRouter(prefix="/tasks", tags=["tasks"])


def _response(aggregate: TaskAggregate) -> TaskResponse:
    task = aggregate.task
    run = aggregate.run
    return TaskResponse(
        id=task.id,
        session_id=task.session_id,
        goal=task.goal,
        status=task.status,
        cancel_requested=task.cancel_requested,
        attempt_count=task.attempt_count,
        created_at=task.created_at,
        updated_at=task.updated_at,
        latest_run=RunResponse.model_validate(run, from_attributes=True),
    )


@router.post("", response_model=TaskResponse, status_code=status.HTTP_202_ACCEPTED)
async def create_task(
    request: TaskCreateRequest,
    service: TaskServiceDependency,
    settings: SettingsDependency,
) -> TaskResponse:
    aggregate = await service.create_task(
        session_id=request.session_id,
        goal=request.goal,
        provider=request.provider or settings.provider.value,
        model=request.model or settings.model or "mock-model",
    )
    return _response(aggregate)


@router.get("/{task_id}", response_model=TaskResponse)
async def get_task(task_id: UUID, service: TaskServiceDependency) -> TaskResponse:
    return _response(await service.get_task(task_id))


@router.post("/{task_id}/{operation}", response_model=TaskResponse)
async def operate_task(
    task_id: UUID,
    operation: Literal["cancel", "pause", "resume"],
    service: TaskServiceDependency,
) -> TaskResponse:
    handlers = {
        "cancel": service.cancel_task,
        "pause": service.pause_task,
        "resume": service.resume_task,
    }
    return _response(await handlers[operation](task_id))
