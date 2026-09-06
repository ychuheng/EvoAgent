"""任务租约的后台心跳。"""

import asyncio

from evoagent.tasks.lease import JobLease, JobLeaseManager, TaskCancellationRequested


class LeaseHeartbeat:
    """按固定间隔续期，直到 Worker 要求停止或租约丢失。"""

    def __init__(self, manager: JobLeaseManager, *, interval_seconds: float) -> None:
        if interval_seconds <= 0:
            raise ValueError("heartbeat interval must be positive")
        self._manager = manager
        self._interval_seconds = interval_seconds

    async def run(self, lease: JobLease, stop: asyncio.Event) -> None:
        current = lease
        while True:
            try:
                await asyncio.wait_for(stop.wait(), timeout=self._interval_seconds)
                return
            except TimeoutError:
                current = await self._manager.heartbeat(current)
                if await self._manager.cancellation_requested(current):
                    raise TaskCancellationRequested("task cancellation was requested") from None
