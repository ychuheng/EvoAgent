"""跨进程原子请求配额与进程内服务并发；等待期间不持有数据库事务。"""

import asyncio
import hashlib
from contextlib import asynccontextmanager

from redis.exceptions import RedisError

from evoagent.providers.base import ProviderError

# 使用 Redis TIME 消除 Worker 时钟偏差；Lua 中的读、补充、扣减不可交错。
TOKEN_BUCKET = """
local clock = redis.call('TIME')
local now = tonumber(clock[1]) + tonumber(clock[2]) / 1000000
local rate, burst = tonumber(ARGV[1]), tonumber(ARGV[2])
local state = redis.call('HMGET', KEYS[1], 'tokens', 'at')
local tokens = tonumber(state[1]) or burst
local at = tonumber(state[2]) or now
tokens = math.min(burst, tokens + math.max(0, now-at)*rate)
local wait = 0
if tokens >= 1 then tokens = tokens-1 else wait = (1-tokens)/rate end
redis.call('HSET', KEYS[1], 'tokens', tokens, 'at', now)
redis.call('PEXPIRE', KEYS[1], math.ceil(burst/rate*1000)+1000)
return tostring(wait)
"""


class RateLimited(ProviderError):
    def __init__(self):
        super().__init__("service quota unavailable within bounded wait", code="rate_limited")


class ServiceGate:
    def __init__(self, settings, client=None):
        self.settings = settings
        self.client = client
        self.semaphores = {}

    async def _quota(self, service):
        if self.client is None:
            return
        digest = hashlib.sha256(service.encode()).hexdigest()
        key = f"{self.settings.redis_namespace}:quota:{digest}"
        while True:
            try:
                delay = float(
                    await self.client.eval(
                        TOKEN_BUCKET,
                        1,
                        key,
                        self.settings.service_requests_per_second,
                        self.settings.service_burst,
                    )
                )
            except (RedisError, OSError, TimeoutError) as error:
                raise RateLimited() from error
            if delay <= 0:
                return
            await asyncio.sleep(min(delay, 0.25))

    @asynccontextmanager
    async def acquire(self, service, check=None):
        semaphore = self.semaphores.setdefault(
            service, asyncio.Semaphore(self.settings.service_concurrency)
        )
        acquired = False
        try:
            try:
                async with asyncio.timeout(self.settings.rate_wait_seconds):
                    await semaphore.acquire()
                    acquired = True
                    await self._quota(service)
            except TimeoutError as error:
                raise RateLimited() from error
            # 等待之后必须重新验证租约；数据库不可用时不会发出外部请求。
            if check is not None:
                await check()
            yield
        finally:
            if acquired:
                semaphore.release()


class GatedProvider:
    def __init__(self, provider, gate, service, check):
        self.provider, self.gate, self.service, self.check = provider, gate, service, check

    async def stream(self, request):
        async with self.gate.acquire(self.service, self.check):
            async for event in self.provider.stream(request):
                yield event
