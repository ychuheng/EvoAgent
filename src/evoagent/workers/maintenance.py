"""独立维护 lane 的空闲退避；不改变正在执行任务的心跳或取消周期。"""

import asyncio

from sqlalchemy.exc import SQLAlchemyError

from evoagent.tasks.lease_guard import LeaseLostError


class MaintenanceLane:
    def __init__(
        self,
        worker,
        wakeup,
        *,
        poll_seconds: float,
        idle_backoff: bool = False,
        drain_immediately: bool = False,
    ):
        if poll_seconds <= 0:
            raise ValueError("maintenance poll interval must be positive")
        self.worker = worker
        self.wakeup = wakeup
        self.poll_seconds = poll_seconds
        self.idle_backoff = idle_backoff
        self.drain_immediately = drain_immediately

    async def run(self, stopping: asyncio.Event):
        delay = 1.0
        while not stopping.is_set():
            try:
                handled = await self.worker.run_once()
            except (SQLAlchemyError, LeaseLostError):
                handled = False
            if handled and (self.idle_backoff or self.drain_immediately):
                delay = 1.0
                continue  # 工作完成立即检查下一项。
            timeout = delay if self.idle_backoff else self.poll_seconds
            notified = await self.wakeup.wait(stopping, timeout)
            if self.idle_backoff:
                delay = 1.0 if notified else min(delay * 2, 8)
