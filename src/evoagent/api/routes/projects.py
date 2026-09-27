"""项目登记、选择、检查与撤销的 HTTP 路由。

对应实施计划 §6 的 P-02：页面与 API 是**唯一**的登记入口，模型不能自行登记宿主路径；
响应里回显 Agent 实际可见的根与当前授权级别，避免"用户以为的范围"和"模型看到的范围"不一致。
"""

from uuid import UUID

from fastapi import APIRouter, HTTPException, status

from evoagent.api.dependencies import ProjectServiceDependency
from evoagent.api.schemas import (
    ProjectAuthorizationRequest,
    ProjectRegisterRequest,
    ProjectResponse,
    ProjectRevokeRequest,
)
from evoagent.projects.schema import (
    ProjectAuthorizationError,
    ProjectNotFoundError,
    ProjectRegistrationError,
)

router = APIRouter(prefix="/projects", tags=["projects"])


def _to_response(record, *, available: bool | None = None) -> ProjectResponse:
    return ProjectResponse(
        id=record.id,
        name=record.name,
        root=record.root,
        authorization=record.authorization,
        status=record.status,
        authorization_version=record.authorization_version,
        created_at=record.created_at,
        root_available=available,
    )


def _translate(error: Exception) -> HTTPException:
    if isinstance(error, ProjectNotFoundError):
        return HTTPException(status.HTTP_404_NOT_FOUND, str(error))
    if isinstance(error, ProjectRegistrationError):
        return HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error))
    if isinstance(error, ProjectAuthorizationError):
        return HTTPException(status.HTTP_409_CONFLICT, str(error))
    return HTTPException(status.HTTP_400_BAD_REQUEST, str(error))


@router.post("", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED)
async def register_project(
    request: ProjectRegisterRequest,
    service: ProjectServiceDependency,
) -> ProjectResponse:
    try:
        record = await service.register(
            path=request.path,
            name=request.name,
            authorization=request.authorization,
        )
        fresh, available = await service.status_report(record.id)
    except (ProjectRegistrationError, ProjectNotFoundError, ProjectAuthorizationError) as error:
        raise _translate(error) from error
    return _to_response(fresh or record, available=available)


@router.get("", response_model=list[ProjectResponse])
async def list_projects(service: ProjectServiceDependency) -> list[ProjectResponse]:
    records = await service.list_projects()
    responses: list[ProjectResponse] = []
    for record in records:
        _fresh, available = await service.status_report(record.id)
        responses.append(_to_response(record, available=available))
    return responses


@router.get("/{project_id}", response_model=ProjectResponse)
async def get_project(project_id: UUID, service: ProjectServiceDependency) -> ProjectResponse:
    try:
        record, available = await service.status_report(project_id)
    except (ProjectNotFoundError, ProjectAuthorizationError) as error:
        raise _translate(error) from error
    return _to_response(record, available=available)


@router.post("/{project_id}/check", response_model=ProjectResponse)
async def check_project(project_id: UUID, service: ProjectServiceDependency) -> ProjectResponse:
    """重新实测根可用性；目录不可用会让新 Task 无法进入旧根。"""

    try:
        record = await service.check(project_id)
        fresh, available = await service.status_report(project_id)
    except (ProjectNotFoundError, ProjectAuthorizationError) as error:
        raise _translate(error) from error
    return _to_response(fresh or record, available=available)


@router.put("/{project_id}/authorization", response_model=ProjectResponse)
async def set_authorization(
    project_id: UUID,
    request: ProjectAuthorizationRequest,
    service: ProjectServiceDependency,
) -> ProjectResponse:
    """提升或降低授权级别；授权版本自增，在跑 Task 的下一次工具调用即被拒绝。"""

    try:
        record = await service.set_authorization(project_id, request.authorization)
    except (ProjectNotFoundError, ProjectAuthorizationError) as error:
        raise _translate(error) from error
    return _to_response(record)


@router.post("/{project_id}/revoke", response_model=ProjectResponse)
async def revoke_project(
    project_id: UUID,
    request: ProjectRevokeRequest,
    service: ProjectServiceDependency,
) -> ProjectResponse:
    """撤销授权；审计事件写入 project_events。"""

    try:
        record = await service.revoke(project_id, reason=request.reason)
    except (ProjectNotFoundError, ProjectAuthorizationError) as error:
        raise _translate(error) from error
    return _to_response(record)
