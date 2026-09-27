import asyncio
from uuid import uuid4

import pytest
from redis.exceptions import ConnectionError

from evoagent.config import Settings
from evoagent.runtime.budget import BudgetScope
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


async def test_budgeted_provider_streams_and_records_usage(tmp_path):
    """M7 发布演练发现的回归：真实模型路径的预算包装层导入错了模块。

    `BudgetedProvider.stream` 里曾写 `from evoagent.providers.base import ProviderEventType`，
    而该名字在 `core.models`，于是**每一次真实模型调用**都在发出请求前抛 ImportError
    （离线 Mock 不经过这个包装，所以只有真实 Provider 才暴露）。这条测试直接跑一次
    "带 usage 的流"，导入错误会立刻让它失败。
    """

    from evoagent.core.models import ProviderEvent, ProviderEventType, TokenUsage
    from evoagent.db.session import Database
    from evoagent.workers.rate_limit import BudgetedProvider

    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'budget.db'}")
    from evoagent.db.base import Base

    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    class FakeProvider:
        async def stream(self, request):
            del request
            yield ProviderEvent(type=ProviderEventType.TEXT_DELTA, text_delta="ok")
            yield ProviderEvent(
                type=ProviderEventType.USAGE,
                usage=TokenUsage(input_tokens=10, output_tokens=5, total_tokens=15),
            )

    provider = BudgetedProvider(
        FakeProvider(),
        settings=Settings(
            _env_file=None,
            budget_scope="trial",
            budget_trial_limit_micros=1_000_000,
            budget_task_limit_micros=1_000_000,
            budget_input_price_micros_per_million=1_000,
            budget_output_price_micros_per_million=1_000,
        ),
        session_factory=database.session_factory,
        scope=BudgetScope.TRIAL,
        task_id=uuid4(),
        run_id=uuid4(),
        provider_name="openai_compatible",
        model="test-model",
    )
    try:
        events = [event async for event in provider.stream(object())]
        assert [event.type for event in events] == [
            ProviderEventType.TEXT_DELTA,
            ProviderEventType.USAGE,
        ]
    finally:
        await database.dispose()


async def test_budgeted_provider_refuses_when_no_limit_is_configured():
    """未填额度时不得发出请求——这正是 M7 降级演练里的"付费闸门拒付"路径。"""

    from evoagent.db.session import Database
    from evoagent.runtime.budget import BudgetExceededError
    from evoagent.workers.rate_limit import BudgetedProvider

    database = Database("sqlite+aiosqlite:///:memory:")
    from evoagent.db.base import Base

    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    class NeverCalled:
        async def stream(self, request):
            raise AssertionError("no request may be issued without a budget")
            yield  # pragma: no cover

    provider = BudgetedProvider(
        NeverCalled(),
        settings=Settings(_env_file=None),
        session_factory=database.session_factory,
        scope=BudgetScope.FORMAL,
        task_id=uuid4(),
        run_id=uuid4(),
        provider_name="openai_compatible",
        model="test-model",
    )
    try:
        with pytest.raises(BudgetExceededError):
            async for _ in provider.stream(object()):
                pytest.fail("no event may be produced")
    finally:
        await database.dispose()
