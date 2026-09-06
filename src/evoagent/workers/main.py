"""一次只处理一个租约任务的后台 Worker。"""

import asyncio
from contextlib import suppress
from typing import Protocol

from evoagent.tasks.lease import (
    JobLease,
    JobLeaseManager,
    LeaseLostError,
    TaskCancellationRequested,
    TaskExecutionResult,
)
from evoagent.tasks.state_machine import PersistentRunStatus
from evoagent.workers.heartbeat import LeaseHeartbeat


class TaskHandler(Protocol):
    async def handle(self, lease: JobLease) -> TaskExecutionResult: ...


class JobWorker:
    """轮询任务、维持租约，并把 Handler 结果安全提交到数据库。"""

    def __init__(
        self,
        *,
        worker_id: str,
        lease_manager: JobLeaseManager,
        handler: TaskHandler,
        heartbeat_seconds: float,
        poll_seconds: float,
    ) -> None:
        self._worker_id = worker_id
        self._lease_manager = lease_manager
        self._handler = handler
        self._heartbeat = LeaseHeartbeat(
            lease_manager,
            interval_seconds=heartbeat_seconds,
        )
        self._poll_seconds = poll_seconds
        self._stopping = asyncio.Event()

    def stop(self) -> None:
        """请求 Worker 在当前短步骤结束后优雅停止。"""

        self._stopping.set()

    async def run_forever(self) -> None:
        while not self._stopping.is_set():
            handled = await self.run_once()
            if not handled:
                with suppress(TimeoutError):
                    await asyncio.wait_for(self._stopping.wait(), timeout=self._poll_seconds)

    async def run_once(self) -> bool:
        await self._lease_manager.promote_due_retries()
        await self._lease_manager.recover_expired()
        lease = await self._lease_manager.claim_next(self._worker_id)
        if lease is None:
            return False

        heartbeat_stop = asyncio.Event()
        heartbeat_task = asyncio.create_task(self._heartbeat.run(lease, heartbeat_stop))
        handler_task = asyncio.create_task(self._handler.handle(lease))
        try:
            done, _pending = await asyncio.wait(
                {heartbeat_task, handler_task}, return_when=asyncio.FIRST_COMPLETED
            )
            if heartbeat_task in done and not handler_task.done():
                error = heartbeat_task.exception()
                handler_task.cancel()
                with suppress(asyncio.CancelledError):
                    await handler_task
                if isinstance(error, TaskCancellationRequested):
                    await self._lease_manager.finalize(
                        lease,
                        TaskExecutionResult(
                            status=PersistentRunStatus.CANCELLED,
                            error_code="cancel_requested",
                            error_message="task cancellation was requested",
                        ),
                    )
                    return True
                if error is not None:
                    raise error
                raise LeaseLostError("heartbeat stopped before task handler completed")

            result = await handler_task
            heartbeat_stop.set()
            try:
                await heartbeat_task
            except TaskCancellationRequested:
                result = TaskExecutionResult(
                    status=PersistentRunStatus.CANCELLED,
                    error_code="cancel_requested",
                    error_message="task cancellation was requested",
                )
            if await self._lease_manager.cancellation_requested(lease):
                result = TaskExecutionResult(
                    status=PersistentRunStatus.CANCELLED,
                    error_code="cancel_requested",
                    error_message="task cancellation was requested",
                )
            await self._lease_manager.finalize(lease, result)
            return True
        finally:
            heartbeat_stop.set()
            for task in (heartbeat_task, handler_task):
                if not task.done():
                    task.cancel()
