"""Task HTTP 路由。"""

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, status

from evoagent.api.dependencies import (
    DatabaseDependency,
    SettingsDependency,
    TaskServiceDependency,
)
from evoagent.api.schemas import (
    InstructionCreateRequest,
    InstructionResponse,
    RunResponse,
    TaskCreateRequest,
    TaskResponse,
)
from evoagent.db.unit_of_work import UnitOfWork
from evoagent.sessions.service import record_instruction
from evoagent.tasks.service import TaskAggregate, TaskNotFoundError

router = APIRouter(prefix="/tasks", tags=["tasks"])


def _response(aggregate: TaskAggregate) -> TaskResponse:
    task = aggregate.task
    run = aggregate.run
    return TaskResponse(
        id=task.id,
        session_id=task.session_id,
        project_id=task.project_id,
        goal=task.goal,
        family=task.family,
        acceptance=task.acceptance,
        frozen_inputs=task.frozen_inputs,
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
        acceptance=request.acceptance,
        provider=request.provider or settings.provider.value,
        model=request.model or settings.model or "mock-model",
        run_mode=request.run_mode,
        project_id=request.project_id,
        project_override="project_id" in request.model_fields_set,
        input_paths=request.input_paths,
        family=request.family,
    )
    return _response(aggregate)


@router.get("/{task_id}", response_model=TaskResponse)
async def get_task(task_id: UUID, service: TaskServiceDependency) -> TaskResponse:
    return _response(await service.get_task(task_id))


@router.post("/{task_id}/instructions", response_model=InstructionResponse)
async def add_instruction(
    task_id: UUID,
    request: InstructionCreateRequest,
    database: DatabaseDependency,
    service: TaskServiceDependency,
) -> InstructionResponse:
    """中途补充约束（I-03）。

    指令持久化后，在**下一个模型/工具边界**注入；它不会改写已经执行或等待审批的动作。
    终止态的 Task 拒绝追加，避免把补充约束塞进已经结束的时间线。
    """

    try:
        aggregate = await service.get_task(task_id)
    except TaskNotFoundError as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from error
    if aggregate.task.status.value in {
        "completed",
        "failed",
        "cancelled",
    } or aggregate.run.status.value in {
        "completed",
        "failed",
        "cancelled",
        "timeout",
        "limit_reached",
        "authorization_revoked",
    }:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "任务已经结束，不能追加运行中约束；请把它作为下一次任务发送",
        )
    async with UnitOfWork(database.session_factory) as unit:
        record = await record_instruction(
            unit.session, task=aggregate.task, content=request.content
        )
        await unit.commit()
    return InstructionResponse(
        id=record.id,
        task_id=task_id,
        content=record.content,
        session_sequence=record.session_sequence,
        created_at=record.created_at,
        injected_at=record.injected_at,
    )


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
