"""一次只处理一个租约任务的后台 Worker。"""

import asyncio
from contextlib import suppress
from typing import Protocol
from uuid import uuid4

from sqlalchemy.exc import SQLAlchemyError

from evoagent.tasks.lease import (
    JobLease,
    JobLeaseManager,
    LeaseLostError,
    TaskCancellationRequested,
    TaskExecutionResult,
)
from evoagent.tasks.state_machine import PersistentRunStatus
from evoagent.workers.heartbeat import LeaseHeartbeat
from evoagent.workers.maintenance import MaintenanceLane
from evoagent.workers.presence import WorkerPresence
from evoagent.workers.recovery import RecoveryScanner
from evoagent.workers.wakeup import Wakeup


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
        snapshot_schema_version: int = 1,
        maintenance_worker=None,
        concurrency: int = 1,
        wakeup: Wakeup | None = None,
        presence: WorkerPresence | None = None,
        recovery_scan_decoupled: bool = False,
        maintenance_idle_backoff: bool = False,
    ) -> None:
        self._worker_id = f"{worker_id[:95]}:{uuid4().hex}"
        self._maintenance_worker = maintenance_worker
        self._maintenance_idle_backoff = maintenance_idle_backoff
        if concurrency < 1:
            raise ValueError("concurrency must be positive")
        self._concurrency = concurrency
        self._wakeup = wakeup or Wakeup(None, "local")
        self._presence = presence
        self._snapshot_schema_version = snapshot_schema_version
        self._lease_manager = lease_manager
        self._handler = handler
        self._heartbeat = LeaseHeartbeat(
            lease_manager,
            interval_seconds=heartbeat_seconds,
        )
        self._poll_seconds = poll_seconds
        self._stopping = asyncio.Event()
        self._recovery = (
            RecoveryScanner(lease_manager, schema_version=snapshot_schema_version)
            if recovery_scan_decoupled
            else None
        )
        self._background_recovery = False

    def stop(self) -> None:
        """请求 Worker 在当前短步骤结束后优雅停止。"""

        self._stopping.set()

    async def run_forever(self) -> None:
        self._background_recovery = self._recovery is not None
        tasks = [asyncio.create_task(self._lane()) for _ in range(self._concurrency)]
        if self._recovery is not None:
            tasks.append(
                asyncio.create_task(
                    self._recovery.run(
                        self._stopping,
                        on_failure=(lambda: self._presence.remove(self._worker_id))
                        if self._presence is not None
                        else None,
                    )
                )
            )
        listener = asyncio.create_task(self._wakeup.listen())
        if self._presence is not None:
            tasks.append(
                asyncio.create_task(
                    self._presence.run(
                        self._worker_id,
                        self._stopping,
                        ready=self._recovery.ready if self._recovery else None,
                    )
                )
            )
        if self._maintenance_worker is not None:
            tasks.append(asyncio.create_task(self._maintenance_lane()))
        try:
            await asyncio.gather(*tasks)
        finally:
            tasks.append(listener)
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            self._background_recovery = False
            await self._wakeup.close()

    async def _maintenance_lane(self):
        if self._maintenance_idle_backoff:
            background = getattr(self._maintenance_worker, "lane", "all") == "background"
            await MaintenanceLane(
                self._maintenance_worker,
                self._wakeup,
                poll_seconds=min(self._poll_seconds, 1),
                idle_backoff=background,
                drain_immediately=True,
            ).run(self._stopping)
            return
        while not self._stopping.is_set():
            with suppress(LeaseLostError, SQLAlchemyError):
                await self._maintenance_worker.run_once()
            with suppress(TimeoutError):
                await asyncio.wait_for(self._stopping.wait(), self._poll_seconds)

    async def _lane(self) -> None:
        while not self._stopping.is_set():
            try:
                handled = await self.run_once()
            except (LeaseLostError, SQLAlchemyError):
                handled = False
            if not handled:
                await self._wakeup.wait(self._stopping, self._poll_seconds)

    async def run_once(self) -> bool:
        if self._recovery is not None:
            if self._background_recovery:
                if not await self._recovery.wait_ready(self._stopping):
                    return False
            else:
                await self._recovery.scan_if_due()
        else:
            await self._lease_manager.promote_due_retries()
            await self._lease_manager.recover_expired()
            await self._lease_manager.recover_pending(schema_version=self._snapshot_schema_version)
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
            await asyncio.gather(heartbeat_task, handler_task, return_exceptions=True)
