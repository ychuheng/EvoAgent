import asyncio

import pytest
from redis.exceptions import ConnectionError

from evoagent.config import Settings
from evoagent.workers.rate_limit import RateLimited, ServiceGate
from evoagent.workers.wakeup import Wakeup


class BrokenRedis:
    async def eval(self, *args):
        raise ConnectionError("unreachable")

    async def publish(self, *args):
        raise ConnectionError("unreachable")


async def test_redis_failure_is_closed_for_quota_but_best_effort_for_wakeup():
    gate = ServiceGate(Settings(_env_file=None), BrokenRedis())
    with pytest.raises(RateLimited):
        async with gate.acquire("model:test"):
            pytest.fail("external request must not execute")
    await Wakeup(BrokenRedis(), "test").publish()


async def test_service_concurrency_and_cancel_release():
    gate = ServiceGate(Settings(_env_file=None, service_concurrency=1, rate_wait_seconds=0.05))
    entered = asyncio.Event()

    async def hold():
        async with gate.acquire("model:test"):
            entered.set()
            await asyncio.Event().wait()

    task = asyncio.create_task(hold())
    await entered.wait()
    with pytest.raises(RateLimited):
        async with gate.acquire("model:test"):
            pytest.fail("concurrency exceeded")
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    async with gate.acquire("model:test"):
        pass


async def test_lease_is_checked_after_wait_before_request():
    gate = ServiceGate(Settings(_env_file=None))

    async def lost():
        raise RuntimeError("lease lost")

    with pytest.raises(RuntimeError, match="lease lost"):
        async with gate.acquire("model:test", lost):
            pytest.fail("stale request dispatched")


async def test_wakeup_has_bounded_fallback_without_redis():
    wakeup = Wakeup(None, "test")
    await asyncio.wait_for(wakeup.wait(asyncio.Event(), 0.01), 0.2)
