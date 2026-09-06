"""Session HTTP 路由。"""

from fastapi import APIRouter, status

from evoagent.api.dependencies import TaskServiceDependency
from evoagent.api.schemas import SessionCreateRequest, SessionResponse

router = APIRouter(prefix="/sessions", tags=["sessions"])


@router.post("", response_model=SessionResponse, status_code=status.HTTP_201_CREATED)
async def create_session(
    request: SessionCreateRequest,
    service: TaskServiceDependency,
) -> SessionResponse:
    record = await service.create_session(request.title)
    return SessionResponse.model_validate(record, from_attributes=True)


@router.get("", response_model=list[SessionResponse])
async def list_sessions(service: TaskServiceDependency) -> list[SessionResponse]:
    records = await service.list_sessions()
    return [SessionResponse.model_validate(record, from_attributes=True) for record in records]
