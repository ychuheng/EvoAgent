"""Session HTTP 路由。"""

from fastapi import APIRouter, HTTPException, status

from evoagent.api.dependencies import TaskServiceDependency
from evoagent.api.schemas import (
    SessionCreateRequest,
    SessionResponse,
    WorkspaceCreateRequest,
    WorkspaceResponse,
)
from evoagent.db.models import DEFAULT_WORKSPACE_ID
from evoagent.tasks.service import SessionNotFoundError

router = APIRouter(prefix="/sessions", tags=["sessions"])
workspaces_router = APIRouter(prefix="/workspaces", tags=["workspaces"])


@workspaces_router.post("", response_model=WorkspaceResponse, status_code=status.HTTP_201_CREATED)
async def create_workspace(
    request: WorkspaceCreateRequest, service: TaskServiceDependency
) -> WorkspaceResponse:
    record = await service.create_workspace(request.name)
    return WorkspaceResponse.model_validate(record, from_attributes=True)


@workspaces_router.get("", response_model=list[WorkspaceResponse])
async def list_workspaces(service: TaskServiceDependency) -> list[WorkspaceResponse]:
    records = await service.list_workspaces()
    return [WorkspaceResponse.model_validate(record, from_attributes=True) for record in records]


@router.post("", response_model=SessionResponse, status_code=status.HTTP_201_CREATED)
async def create_session(
    request: SessionCreateRequest,
    service: TaskServiceDependency,
) -> SessionResponse:
    try:
        record = await service.create_session(
            request.title, request.workspace_id or DEFAULT_WORKSPACE_ID
        )
    except SessionNotFoundError as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from error
    return SessionResponse.model_validate(record, from_attributes=True)


@router.get("", response_model=list[SessionResponse])
async def list_sessions(service: TaskServiceDependency) -> list[SessionResponse]:
    records = await service.list_sessions()
    return [SessionResponse.model_validate(record, from_attributes=True) for record in records]
