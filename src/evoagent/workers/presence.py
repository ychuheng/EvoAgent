"""Best-effort task-worker presence; task leases remain the execution authority."""

import asyncio
from contextlib import suppress

from redis.asyncio import Redis
from redis.exceptions import RedisError


class WorkerPresence:
    def __init__(self, client: Redis | None, namespace: str) -> None:
        self._client = client
        self._prefix = f"{namespace}:task-worker:"

    async def refresh(self, worker_id: str) -> None:
        if self._client is None:
            return
        with suppress(RedisError, OSError, TimeoutError):
            async with asyncio.timeout(1):
                await self._client.set(f"{self._prefix}{worker_id}", "online", ex=10)

    async def remove(self, worker_id: str) -> None:
        if self._client is None:
            return
        with suppress(RedisError, OSError, TimeoutError):
            async with asyncio.timeout(1):
                await self._client.delete(f"{self._prefix}{worker_id}")

    async def run(self, worker_id: str, stopping: asyncio.Event) -> None:
        try:
            while not stopping.is_set():
                await self.refresh(worker_id)
                with suppress(TimeoutError):
                    await asyncio.wait_for(stopping.wait(), timeout=2)
        finally:
            await self.remove(worker_id)

    async def is_ready(self) -> bool | None:
        """Return None when Redis cannot be checked, not a false offline verdict."""

        if self._client is None:
            return None
        try:
            async with asyncio.timeout(1):
                async for _key in self._client.scan_iter(match=f"{self._prefix}*", count=10):
                    return True
        except (RedisError, OSError, TimeoutError):
            return None
        return False
