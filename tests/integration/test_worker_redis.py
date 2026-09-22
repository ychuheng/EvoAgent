import asyncio
import os
from uuid import uuid4

import pytest
from redis.asyncio import Redis

from evoagent.workers.rate_limit import TOKEN_BUCKET
from evoagent.workers.wakeup import Wakeup


@pytest.mark.redis
async def test_real_redis_atomic_bucket_and_duplicate_wakeup():
    url = os.getenv("EVOAGENT_TEST_REDIS_URL")
    if not url:
        pytest.skip("EVOAGENT_TEST_REDIS_URL is not configured")
    first, second = Redis.from_url(url), Redis.from_url(url)
    namespace = "acceptance-" + uuid4().hex
    key = namespace + ":quota"
    bus = Wakeup(first, namespace)
    listener = asyncio.create_task(bus.listen())
    try:
        results = await asyncio.gather(
            *[client.eval(TOKEN_BUCKET, 1, key, 0.001, 3) for client in [first, second] * 10]
        )
        assert sum(float(value) == 0 for value in results) == 3
        for _ in range(20):
            await bus.publish()
            if bus.event.is_set():
                break
            await asyncio.sleep(0.02)
        assert bus.event.is_set()
        # 重复消息只会合并成提示，不能创建/领取额外 Task。
        await bus.publish()
        await asyncio.wait_for(bus.wait(asyncio.Event(), 0.2), 0.5)
    finally:
        listener.cancel()
        await asyncio.gather(listener, return_exceptions=True)
        await first.delete(key)
        await first.aclose()
        await second.aclose()
