import asyncio

import pytest

from evoagent.core.runtime_metrics import (
    collect_runtime_metrics,
    observe_event_buffer,
    observe_scan_cache,
)
from evoagent.privacy.redaction import POLICY_VERSION, RedactionResult
from evoagent.privacy.scanner import BoundedScanner, ScanLimits, ScanUnavailable


async def test_task_scopes_isolate_counts_and_restore_nested_scope():
    async def measured(size):
        with collect_runtime_metrics() as metrics:

            async def child():
                observe_event_buffer(size)
                observe_scan_cache(hit=True)

            await asyncio.create_task(child())
            with collect_runtime_metrics() as nested:
                observe_event_buffer(999)
                observe_scan_cache(miss=True)
            observe_event_buffer(0)
            assert nested.report()["event_buffer_peak_bytes"] == 999
            return metrics.report()

    one, two = await asyncio.gather(measured(10), measured(20))
    assert one == dict(
        event_buffer_peak_bytes=10, scan_cache_hits=1, scan_cache_misses=0, scan_cache_evictions=0
    )
    assert two["event_buffer_peak_bytes"] == 20
    assert two["scan_cache_hits"] == 1
    # Outside collection nothing is retained, even after exceptional exit.
    with pytest.raises(ValueError), collect_runtime_metrics():
        raise ValueError("fixture")
    observe_event_buffer(999)
    with collect_runtime_metrics() as fresh:
        assert not any(fresh.report().values())


async def test_cache_counts_real_lookup_eviction_and_failure_without_retaining_text(monkeypatch):
    scanner = BoundedScanner()

    async def scan(text, limits, *, deadline=None):
        if text == "failure":
            raise ScanUnavailable("scan_failed")
        return RedactionResult(text="", categories=(), policy_version=POLICY_VERSION)

    monkeypatch.setattr(scanner, "scan", scan)
    with collect_runtime_metrics() as metrics:
        for index in range(257):
            await scanner.scan_identical(f"fixture {index}", ScanLimits())
        await scanner.scan_identical("fixture 256", ScanLimits())
        with pytest.raises(ScanUnavailable):
            await scanner.scan_identical("failure", ScanLimits())
        with pytest.raises(ScanUnavailable):
            await scanner.scan_identical("fixture 256", ScanLimits(max_bytes=1))
        assert metrics.report() == dict(
            event_buffer_peak_bytes=0,
            scan_cache_hits=1,
            scan_cache_misses=258,
            scan_cache_evictions=1,
        )
        assert "fixture" not in str(metrics.report())
    with collect_runtime_metrics() as off:
        await scanner.scan("fixture 256", ScanLimits())
        assert not any(off.report().values())
