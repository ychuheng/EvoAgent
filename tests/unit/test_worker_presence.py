import asyncio

import pytest
from redis.exceptions import RedisError

from evoagent.workers.presence import WorkerPresence


class FakeRedis:
    def __init__(self) -> None:
        self.keys: dict[str, str] = {}
        self.fail = False

    async def set(self, key: str, value: str, *, ex: int) -> None:
        assert ex == 10
        self.keys[key] = value

    async def delete(self, key: str) -> None:
        self.keys.pop(key, None)

    async def scan_iter(self, *, match: str, count: int):
        if self.fail:
            raise RedisError("offline")
        assert count == 10
        prefix = match.removesuffix("*")
        for key in self.keys:
            if key.startswith(prefix):
                yield key


@pytest.mark.asyncio
async def test_presence_reports_unknown_without_redis_or_when_redis_fails() -> None:
    assert await WorkerPresence(None, "test").is_ready() is None
    client = FakeRedis()
    presence = WorkerPresence(client, "test")
    client.fail = True
    assert await presence.is_ready() is None


@pytest.mark.asyncio
async def test_presence_lifecycle_marks_only_task_worker_and_clears_on_stop() -> None:
    client = FakeRedis()
    presence = WorkerPresence(client, "test")
    assert await presence.is_ready() is False
    client.keys["test:other-worker:1"] = "online"
    assert await presence.is_ready() is False

    stopping = asyncio.Event()
    task = asyncio.create_task(presence.run("worker-1", stopping))
    try:
        for _ in range(20):
            if await presence.is_ready():
                break
            await asyncio.sleep(0.01)
        assert await presence.is_ready() is True
    finally:
        stopping.set()
        await task
    assert await presence.is_ready() is False
