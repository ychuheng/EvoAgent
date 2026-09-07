"""Task 已提交事件的 SSE 路由。"""

from collections.abc import AsyncIterator
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, Request
from fastapi.sse import EventSourceResponse, ServerSentEvent

from evoagent.api.dependencies import SettingsDependency, TaskServiceDependency
from evoagent.trace.sse import SseEventService

router = APIRouter(prefix="/tasks", tags=["events"])


@router.get("/{task_id}/events", response_class=EventSourceResponse)
async def stream_task_events(
    task_id: UUID,
    request: Request,
    settings: SettingsDependency,
    tasks: TaskServiceDependency,
    last_event_id: Annotated[int | None, Header(alias="Last-Event-ID", ge=0)] = None,
) -> AsyncIterator[ServerSentEvent]:
    aggregate = await tasks.get_task(task_id)
    service = SseEventService(
        request.app.state.database.session_factory,
        poll_seconds=settings.sse_poll_seconds,
        heartbeat_seconds=settings.sse_heartbeat_seconds,
    )
    async for event in service.stream(aggregate.run.id, after_sequence=last_event_id or 0):
        yield event
