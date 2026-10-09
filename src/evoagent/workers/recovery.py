"""每进程唯一、有界且公平轮转的恢复扫描；领取槽不各自扫描。"""

import asyncio
import time
from contextlib import suppress

from sqlalchemy.exc import SQLAlchemyError


class RecoveryScanner:
    def __init__(self, manager, *, schema_version: int, interval: float = 2, batch_size: int = 50):
        if interval <= 0 or not 1 <= batch_size <= 50:
            raise ValueError("invalid recovery scan budget")
        self.manager = manager
        self.schema_version = schema_version
        self.interval = interval
        self.batch_size = batch_size
        self.ready = asyncio.Event()
        self._lock = asyncio.Lock()
        self._last_scan = float("-inf")
        self.failures = 0
        self.batches = 0

    async def scan_once(self, *, only_if_due: bool = False) -> bool:
        async with self._lock:
            if (
                only_if_due
                and self.ready.is_set()
                and time.monotonic() - self._last_scan < self.interval
            ):
                return False
            try:
                counts = []
                for operation in (
                    lambda: self.manager.promote_due_retries(limit=self.batch_size),
                    lambda: self.manager.recover_expired(limit=self.batch_size),
                    lambda: self.manager.recover_pending(
                        schema_version=self.schema_version, limit=self.batch_size, skip_locked=True
                    ),
                ):
                    counts.append(await operation())
                    await asyncio.sleep(0)  # 轮流处理三类，积压不能霸占事件循环。
                self.batches += 1
                self._last_scan = time.monotonic()
                self.ready.set()
                return any(count >= self.batch_size for count in counts)
            except asyncio.CancelledError:
                self.ready.clear()
                raise
            except Exception:
                self.ready.clear()
                self.failures += 1
                raise

    async def scan_if_due(self):
        # run_once 的独立调用仍按进程共用的时间预算补扫，不每次领取前全扫。
        await self.scan_once(only_if_due=True)

    async def run(self, stopping: asyncio.Event, *, on_failure=None):
        backoff = 1
        while not stopping.is_set():
            try:
                scan = asyncio.create_task(self.scan_once())
                stopped = asyncio.create_task(stopping.wait())
                try:
                    done, _ = await asyncio.wait(
                        (scan, stopped), return_when=asyncio.FIRST_COMPLETED
                    )
                    if stopped in done:
                        return
                    backlog = await scan
                finally:
                    for task in (scan, stopped):
                        if not task.done():
                            task.cancel()
                    await asyncio.gather(scan, stopped, return_exceptions=True)
                backoff = 1
                if backlog:
                    continue
                delay = self.interval
            except SQLAlchemyError:
                if on_failure is not None:
                    await on_failure()
                delay = backoff
                backoff = min(backoff * 2, 8)
            with suppress(TimeoutError):
                await asyncio.wait_for(stopping.wait(), timeout=delay)

    async def wait_ready(self, stopping: asyncio.Event) -> bool:
        tasks = [asyncio.create_task(event.wait()) for event in (self.ready, stopping)]
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            return self.ready.is_set() and not stopping.is_set()
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
