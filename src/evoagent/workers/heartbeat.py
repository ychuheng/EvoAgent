"""任务租约的后台心跳。"""

import asyncio

from evoagent.tasks.lease import JobLease, JobLeaseManager, TaskCancellationRequested


class LeaseHeartbeat:
    """按固定间隔续期，直到 Worker 要求停止或租约丢失。"""

    def __init__(
        self, manager: JobLeaseManager, *, interval_seconds: float, cancellation_notifier=None
    ) -> None:
        if interval_seconds <= 0:
            raise ValueError("heartbeat interval must be positive")
        self._manager = manager
        self._interval_seconds = interval_seconds
        self._cancellation_notifier = cancellation_notifier

    async def run(self, lease: JobLease, stop: asyncio.Event) -> None:
        if self._cancellation_notifier is not None:
            with self._cancellation_notifier.register(lease.task_id) as signal:
                await self._run_with_status(lease, stop, signal)
            return
        current = lease
        while True:
            try:
                await asyncio.wait_for(stop.wait(), timeout=self._interval_seconds)
                return
            except TimeoutError:
                current = await self._manager.heartbeat(current)
                if await self._manager.cancellation_requested(current):
                    raise TaskCancellationRequested("task cancellation was requested") from None

    async def _run_with_status(self, lease, stop, signal):
        current = lease
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._interval_seconds
        while True:
            tasks = [asyncio.create_task(event.wait()) for event in (stop, signal)]
            try:
                await asyncio.wait(
                    tasks,
                    timeout=max(0, deadline - loop.time()),
                    return_when=asyncio.FIRST_COMPLETED,
                )
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
            if stop.is_set():
                return
            if signal.is_set():
                signal.clear()
                # The hint never acts as authorization or a cached DB value.
                if await self._manager.cancellation_requested(current):
                    raise TaskCancellationRequested("task cancellation was requested")
            if loop.time() < deadline:
                continue
            if self._cancellation_notifier.healthy:
                status = await self._manager.heartbeat_and_status(current)
                current = status.lease
                if status.cancel_requested:
                    raise TaskCancellationRequested("task cancellation was requested")
            else:
                # Missing trigger, SQLite, disconnect or startup: preserve the
                # old post-commit cancellation query, including its race window.
                current = await self._manager.heartbeat(current)
                if await self._manager.cancellation_requested(current):
                    raise TaskCancellationRequested("task cancellation was requested")
            deadline = loop.time() + self._interval_seconds
