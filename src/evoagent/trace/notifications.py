"""已提交事件的有界唤醒提示；Redis 与内存都不是事件事实来源。"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field
from uuid import UUID

from redis.asyncio import Redis
from redis.exceptions import RedisError


@dataclass
class _RunSignal:
    generation: int = 0
    readers: int = 0
    changed: asyncio.Condition = field(default_factory=asyncio.Condition)


class RunEventNotifier:
    def __init__(self, client: Redis | None, namespace: str, *, capacity: int = 1024):
        if capacity < 1:
            raise ValueError("notification capacity must be positive")
        self.client = client
        self.channel = f"{namespace}:run-events"
        self._capacity = capacity
        self._signals: dict[UUID, _RunSignal] = {}
        self._pending: set[UUID] = set()
        self._publisher: asyncio.Task | None = None
        self._listener: asyncio.Task | None = None
        self._ready = asyncio.Event()
        self._closed = False
        self.dropped_hints = 0

    async def start(self):
        if self._listener is None:
            self._listener = asyncio.create_task(self.listen())
        # 监听不可用时有界退回 DB 兜底，不阻止 API 启动。
        with suppress(TimeoutError):
            await asyncio.wait_for(self._ready.wait(), timeout=1)

    @asynccontextmanager
    async def subscribe(self, run_id: UUID) -> AsyncIterator[bool]:
        signal = self._signals.get(run_id)
        if signal is None and not self._closed and len(self._signals) < self._capacity:
            signal = self._signals[run_id] = _RunSignal()
        if signal is None:
            yield False  # 容量用尽时继续有界 DB 补读，不无限创建协调器。
            return
        signal.readers += 1
        try:
            yield True
        finally:
            signal.readers -= 1
            if not signal.readers:
                self._signals.pop(run_id, None)

    def generation(self, run_id: UUID) -> int:
        signal = self._signals.get(run_id)
        return signal.generation if signal else 0

    async def _notify(self, run_id: UUID):
        signal = self._signals.get(run_id)
        if signal is not None:
            async with signal.changed:
                signal.generation += 1
                signal.changed.notify_all()

    def schedule_publish(self, run_ids: set[UUID]):
        """仅在外层事务成功提交后调用；有界合并，不为每个事件创建 Task。"""
        if self._closed:
            return
        for run_id in run_ids:
            if len(self._pending) < self._capacity:
                self._pending.add(run_id)
            elif run_id not in self._pending:
                self.dropped_hints += 1
        if self._pending and (self._publisher is None or self._publisher.done()):
            self._publisher = asyncio.create_task(self._publish_pending())

    async def _publish_pending(self):
        while self._pending and not self._closed:
            await self.publish(self._pending.pop())

    async def publish(self, run_id: UUID):
        await self._notify(run_id)
        if self.client is not None and not self._closed:
            with suppress(RedisError, OSError, TimeoutError):
                async with asyncio.timeout(1):
                    await self.client.publish(self.channel, str(run_id))

    async def listen(self):
        if self.client is None:
            self._ready.set()
            return
        while not self._closed:
            try:
                async with self.client.pubsub() as subscription:
                    await subscription.subscribe(self.channel)
                    self._ready.set()
                    async for message in subscription.listen():
                        if message["type"] != "message":
                            continue
                        try:
                            value = message["data"]
                            run_id = UUID(value.decode() if isinstance(value, bytes) else value)
                        except (ValueError, TypeError, UnicodeError, AttributeError):
                            continue
                        await self._notify(run_id)
            except (RedisError, OSError, TimeoutError):
                await asyncio.sleep(1)

    async def wait(self, run_id: UUID, observed_generation: int, timeout: float):
        signal = self._signals.get(run_id)
        if signal is None or self._closed:
            await asyncio.sleep(timeout)
            return
        async with signal.changed:
            with suppress(TimeoutError):
                await asyncio.wait_for(
                    signal.changed.wait_for(
                        lambda: signal.generation != observed_generation or self._closed
                    ),
                    timeout=timeout,
                )

    async def close(self):
        self._closed = True
        for run_id in tuple(self._signals):
            await self._notify(run_id)
        tasks = [task for task in (self._listener, self._publisher) if task is not None]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._pending.clear()
