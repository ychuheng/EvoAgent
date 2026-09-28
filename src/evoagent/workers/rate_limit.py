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


class ServiceDependencyUnavailable(ProviderError):
    """配额所依赖的 Redis 不可用——**不是**限流，也不是模型的错。

    早先这条路径复用 `RateLimited`，运维看到 `rate_limited` 会去查服务商限流，而真实原因是
    依赖不可用。行为不变：仍然 fail-closed（外部请求一次都不会发出），只是错误码如实表达；
    它在 `runtime/retry.py` 里被归为瞬时错误，因此 Redis 恢复后任务会按退避重试并自行完成。
    """

    def __init__(self, message: str = "service quota dependency unavailable") -> None:
        super().__init__(message, code="dependency_unavailable")


class ServiceGate:
    def __init__(self, settings, client=None):
        self.settings = settings
        self.client = client
        self.semaphores = {}

    async def _quota(self, service, answered: list[bool] | None = None):
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
                # 依赖不可用（Redis 宕、网络断）区别于"等不到配额"：错误码必须让运维看得出
                # 该去修依赖还是该降速。这里仍然不发外部请求。
                raise ServiceDependencyUnavailable(
                    f"quota dependency unavailable: {type(error).__name__}"
                ) from error
            if answered is not None:
                # 配额服务答复过至少一次：之后即使等待窗口用尽，也是**真的**在限流。
                answered[0] = True
            if delay <= 0:
                return
            await asyncio.sleep(min(delay, 0.25))

    @asynccontextmanager
    async def acquire(self, service, check=None):
        semaphore = self.semaphores.setdefault(
            service, asyncio.Semaphore(self.settings.service_concurrency)
        )
        acquired = False
        entered_quota = False
        answered: list[bool] = [False]
        try:
            try:
                async with asyncio.timeout(self.settings.rate_wait_seconds):
                    await semaphore.acquire()
                    acquired = True
                    entered_quota = True
                    await self._quota(service, answered)
            except TimeoutError as error:
                if entered_quota and not answered[0]:
                    # 走到了配额检查、而配额服务一次都没答复：这是依赖不可用（Redis 挂死、
                    # 连接被黑洞），不是服务商限流。不能用等待超时把它说成限流。
                    raise ServiceDependencyUnavailable(
                        "quota dependency did not answer within the bounded wait"
                    ) from error
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


class BudgetedProvider:
    """在付费模型调用前后执行 M0c 预算闸门。

    - **调用前**：查该 scope 与 Task 的累计花费；额度或价格假设未填、或已达停止阈值即拒绝，
      因此默认配置（全未填）下付费路径是关闭的。
    - **调用后**：按 provider 上报的 usage 记账；金额为微元整数，逐次留痕。

    依赖由装配方（Worker bootstrap）注入，这里不假设 provider 暴露额外属性。
    """

    def __init__(
        self,
        provider,
        *,
        settings,
        session_factory,
        scope,
        task_id,
        run_id,
        provider_name: str,
        model: str,
    ):
        self.provider = provider
        self.settings = settings
        self.session_factory = session_factory
        self.scope = scope
        self.task_id = task_id
        self.run_id = run_id
        self.provider_name = provider_name
        self.model = model

    async def stream(self, request):
        # `ProviderEventType` 在 `core.models` 里，不在 `providers.base`：写错会让
        # **每一次真实模型调用**在发出请求前抛 ImportError（离线 Mock 不走这条包装路径，
        # 所以只有真实 Provider 才暴露；M7 发布演练实测到 `internal_provider_error`）。
        from evoagent.core.models import ProviderEventType
        from evoagent.runtime.budget import (
            BudgetExceededError,
            evaluate_budget,
            limits_from_settings,
            record_spend,
        )

        async with self.session_factory() as session:
            status = await evaluate_budget(
                session, self.settings, scope=self.scope, task_id=self.task_id
            )
        if not status.allowed:
            raise BudgetExceededError(status.reason)
        limits = limits_from_settings(self.settings, self.scope)

        usage = None
        async for event in self.provider.stream(request):
            if event.type is ProviderEventType.USAGE and event.usage is not None:
                usage = event.usage
            yield event
        if usage is not None:
            await record_spend(
                self.session_factory,
                scope=self.scope,
                task_id=self.task_id,
                run_id=self.run_id,
                provider=self.provider_name,
                model=self.model,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                limits=limits,
            )
