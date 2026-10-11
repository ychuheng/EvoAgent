"""Opt-in, task-local numeric telemetry; never store payloads or authority."""

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass


@dataclass
class RuntimeMetrics:
    event_buffer_peak_bytes: int = 0
    scan_cache_hits: int = 0
    scan_cache_misses: int = 0
    scan_cache_evictions: int = 0

    def report(self):
        return asdict(self)


_CURRENT: ContextVar[RuntimeMetrics | None] = ContextVar("runtime_metrics", default=None)


@contextmanager
def collect_runtime_metrics():
    """Child tasks share this scope; independent tasks do not share counters."""
    metrics = RuntimeMetrics()
    token = _CURRENT.set(metrics)
    try:
        yield metrics
    finally:
        _CURRENT.reset(token)


def observe_event_buffer(size: int):
    metrics = _CURRENT.get()
    if metrics is not None:
        metrics.event_buffer_peak_bytes = max(metrics.event_buffer_peak_bytes, size)


def observe_scan_cache(*, hit=False, miss=False, eviction=False):
    metrics = _CURRENT.get()
    if metrics is not None:
        metrics.scan_cache_hits += int(hit)
        metrics.scan_cache_misses += int(miss)
        metrics.scan_cache_evictions += int(eviction)
