"""Session HTTP 路由。"""

from uuid import UUID

from fastapi import APIRouter, HTTPException, status

from evoagent.api.dependencies import ProjectServiceDependency, TaskServiceDependency
from evoagent.api.schemas import (
    SessionCreateRequest,
    SessionResponse,
    SessionUpdateRequest,
    WorkspaceCreateRequest,
    WorkspaceResponse,
)
from evoagent.db.models import DEFAULT_WORKSPACE_ID
from evoagent.projects.schema import ProjectAuthorizationError, ProjectNotFoundError
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
    projects: ProjectServiceDependency,
) -> SessionResponse:
    try:
        record = await service.create_session(
            request.title,
            request.workspace_id or DEFAULT_WORKSPACE_ID,
            project_id=request.project_id,
        )
    except SessionNotFoundError as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from error
    except (ProjectNotFoundError, ProjectAuthorizationError) as error:
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error
    if request.project_id is not None:
        # 会话绑定只影响之后创建的 Task；这里二次确认项目仍可用。
        await projects.check(request.project_id)
    return SessionResponse.model_validate(record, from_attributes=True)


@router.get("", response_model=list[SessionResponse])
async def list_sessions(service: TaskServiceDependency) -> list[SessionResponse]:
    records = await service.list_sessions()
    return [SessionResponse.model_validate(record, from_attributes=True) for record in records]


@router.put("/{session_id}/project", response_model=SessionResponse)
async def select_session_project(
    session_id: UUID,
    request: SessionUpdateRequest,
    projects: ProjectServiceDependency,
) -> SessionResponse:
    """切换会话选中的项目。

    切换**不改变在跑 Task**：Task 创建时就冻结了项目绑定与授权版本。
    """

    try:
        record = await projects.bind_session(session_id, request.project_id)
    except ProjectNotFoundError as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from error
    except ProjectAuthorizationError as error:
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error
    return SessionResponse.model_validate(record, from_attributes=True)
