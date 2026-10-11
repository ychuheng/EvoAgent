"""Benchmark-only sampling; never retain SQL parameters or payload content."""

import asyncio
import json
import os
import sys
from pathlib import Path


def json_bytes(value):
    """Canonical UTF-8 content bytes, not PostgreSQL storage/network bytes."""
    return len(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    )


def current_rss_bytes():
    if not sys.platform.startswith("linux"):
        return None
    try:
        resident_pages = int(Path("/proc/self/statm").read_text().split()[1])
        return resident_pages * os.sysconf("SC_PAGE_SIZE")
    except (OSError, ValueError, IndexError):
        return None


class RuntimeResourceProbe:
    """10ms sampled parent RSS and event-loop lag; excludes scanner children."""

    def __init__(self):
        self.rss_peak = current_rss_bytes()
        self.lags = []
        self.task = None

    async def __aenter__(self):
        self.task = asyncio.create_task(self._sample())
        await asyncio.sleep(0)
        return self

    async def _sample(self):
        loop = asyncio.get_running_loop()
        while True:
            deadline = loop.time() + 0.01
            await asyncio.sleep(0.01)
            self.lags.append(max(0.0, (loop.time() - deadline) * 1000))
            rss = current_rss_bytes()
            if rss is not None:
                self.rss_peak = max(self.rss_peak or 0, rss)

    async def __aexit__(self, *_args):
        self.task.cancel()
        await asyncio.gather(self.task, return_exceptions=True)

    def report(self):
        ordered = sorted(self.lags)
        return {
            "parent_rss_sampled_peak_bytes": self.rss_peak,
            "event_loop_lag_samples": len(ordered),
            "event_loop_lag_max_ms": max(ordered) if ordered else None,
            "event_loop_lag_p95_ms": ordered[int((len(ordered) - 1) * 0.95)] if ordered else None,
        }
