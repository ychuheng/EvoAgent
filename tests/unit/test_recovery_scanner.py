import asyncio
import importlib.util
from collections import Counter
from pathlib import Path

import pytest
from sqlalchemy.exc import SQLAlchemyError

from evoagent.workers.main import JobWorker
from evoagent.workers.recovery import RecoveryScanner


class Manager:
    def __init__(self):
        self.calls = []
        self.claims = 0
        self.gate = None
        self.fail = False
        self.backlog = False

    async def promote_due_retries(self, *, limit=100):
        self.calls.append(("retry", limit))
        if self.gate is not None:
            await self.gate.wait()
        return 50 if self.backlog else 0

    async def recover_expired(self, *, limit=100):
        self.calls.append(("expired", limit))
        if self.fail:
            raise SQLAlchemyError("offline")
        return 0

    async def recover_pending(self, *, schema_version=1, limit=100, skip_locked=False):
        self.calls.append(("pending", limit))
        return 0

    async def claim_next(self, worker):
        self.claims += 1
        return None


def worker(manager, *, enabled):
    return JobWorker(
        worker_id="scan-test",
        lease_manager=manager,
        handler=None,
        heartbeat_seconds=10,
        poll_seconds=0.01,
        concurrency=4,
        recovery_scan_decoupled=enabled,
    )


async def test_manual_claims_share_one_scan_and_off_retains_reference_calls():
    old = Manager()
    reference = worker(old, enabled=False)
    for _ in range(4):
        assert not await reference.run_once()
    assert old.calls == [(kind, 100) for _ in range(4) for kind in ("retry", "expired", "pending")]
    spec = importlib.util.spec_from_file_location(
        "pre_p6a_worker", Path(__file__).parents[1] / "fixtures/runtime/reference_worker.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    oracle_manager = Manager()
    oracle = module.JobWorker(
        worker_id="oracle",
        lease_manager=oracle_manager,
        handler=None,
        heartbeat_seconds=10,
        poll_seconds=0.01,
    )
    for _ in range(4):
        assert not await oracle.run_once()
    assert oracle_manager.calls == old.calls and oracle_manager.claims == old.claims
    new = Manager()
    optimized = worker(new, enabled=True)
    await asyncio.gather(*(optimized.run_once() for _ in range(10)))
    assert new.calls == [(kind, 50) for kind in ("retry", "expired", "pending")]
    assert new.claims == 10


async def test_startup_scan_is_barrier_for_all_claim_slots_and_stop_is_prompt():
    manager = Manager()
    manager.gate = asyncio.Event()
    runtime = worker(manager, enabled=True)
    running = asyncio.create_task(runtime.run_forever())
    try:
        await asyncio.sleep(0.03)
        assert manager.claims == 0 and not runtime._recovery.ready.is_set()
        manager.gate.set()
        async with asyncio.timeout(1):
            while not manager.claims:
                await asyncio.sleep(0.01)
        # 已就绪后多个领取槽继续领取，不重复跑恢复查询。
        await asyncio.sleep(0.04)
        assert Counter(kind for kind, _ in manager.calls) == {
            "retry": 1,
            "expired": 1,
            "pending": 1,
        }
    finally:
        runtime.stop()
        await asyncio.wait_for(running, timeout=0.5)


async def test_stop_interrupts_startup_wait_before_ready():
    manager = Manager()
    manager.gate = asyncio.Event()
    runtime = worker(manager, enabled=True)
    running = asyncio.create_task(runtime.run_forever())
    await asyncio.sleep(0.02)
    runtime.stop()
    await asyncio.wait_for(running, timeout=0.5)
    assert manager.claims == 0


async def test_failed_scan_clears_ready_and_does_not_advertise_health():
    manager = Manager()
    scanner = RecoveryScanner(manager, schema_version=2)
    await scanner.scan_once()
    assert scanner.ready.is_set()
    manager.fail = True
    with pytest.raises(SQLAlchemyError):
        await scanner.scan_once()
    assert not scanner.ready.is_set() and scanner.failures == 1
    stopping = asyncio.Event()
    waiter = asyncio.create_task(scanner.wait_ready(stopping))
    await asyncio.sleep(0.01)
    assert not waiter.done()
    stopping.set()
    assert not await waiter


async def test_backlog_rotates_classes_and_yields_between_bounded_batches():
    manager = Manager()
    manager.backlog = True
    scanner = RecoveryScanner(manager, schema_version=2)
    stopping = asyncio.Event()
    running = asyncio.create_task(scanner.run(stopping))
    async with asyncio.timeout(1):
        while scanner.batches < 5:
            await asyncio.sleep(0)
    stopping.set()
    await running
    assert all(limit <= 50 for _, limit in manager.calls)
    assert [kind for kind, _ in manager.calls] == ["retry", "expired", "pending"] * scanner.batches


async def test_error_backoff_is_bounded_and_can_stop_without_waiting():
    manager = Manager()
    manager.fail = True
    scanner = RecoveryScanner(manager, schema_version=2)
    stopping = asyncio.Event()
    failure_observed = asyncio.Event()

    async def failure():
        assert not scanner.ready.is_set()
        failure_observed.set()

    running = asyncio.create_task(scanner.run(stopping, on_failure=failure))
    await failure_observed.wait()
    await asyncio.sleep(0.02)
    assert scanner.failures == 1  # 未因异常变成热循环。
    stopping.set()
    await asyncio.wait_for(running, timeout=0.2)
