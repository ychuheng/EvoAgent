"""Redis 消息只是提示；订阅中断或丢消息后仍按固定周期扫描数据库。"""

import asyncio
from contextlib import suppress

from redis.asyncio import Redis
from redis.exceptions import RedisError


class Wakeup:
    def __init__(self, client: Redis | None, namespace: str):
        self.client = client
        self.channel = f"{namespace}:queue-wakeup"
        self.event = asyncio.Event()

    async def publish(self):
        if self.client is not None:
            with suppress(RedisError, OSError, TimeoutError):
                async with asyncio.timeout(1):
                    await self.client.publish(self.channel, "scan")

    async def listen(self):
        if self.client is None:
            return
        while True:
            try:
                async with self.client.pubsub() as subscription:
                    await subscription.subscribe(self.channel)
                    async for message in subscription.listen():
                        if message["type"] == "message":
                            self.event.set()
            except (RedisError, OSError, TimeoutError):
                await asyncio.sleep(1)

    async def wait(self, stopping: asyncio.Event, seconds: float):
        tasks = [asyncio.create_task(event.wait()) for event in (self.event, stopping)]
        try:
            await asyncio.wait(tasks, timeout=seconds, return_when=asyncio.FIRST_COMPLETED)
            self.event.clear()
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)


def redis_client(settings):
    if settings.redis_url is None or not settings.redis_url.get_secret_value():
        return None
    return Redis.from_url(
        settings.redis_url.get_secret_value(), socket_connect_timeout=1, socket_timeout=1
    )
