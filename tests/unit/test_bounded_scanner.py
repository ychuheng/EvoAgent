import asyncio
import sys

import pytest

from evoagent.privacy.scanner import BoundedScanner, ScanLimits, ScanUnavailable


@pytest.mark.asyncio
async def test_worker_checks_real_rules():
    scanner = BoundedScanner()
    result = await scanner.scan("postgres://fake:fake@localhost/example", ScanLimits())
    assert result.categories == ("dsn_credentials",)
    assert not (await scanner.scan("normal data", ScanLimits())).redacted
    quoted = await scanner.scan('{"password":"fixture only value"}', ScanLimits())
    assert quoted.categories == ("quoted_json_credential",)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["timeout", "cpu", "cancel"])
async def test_scanner_reaps_child_before_releasing_slot(mode, tmp_path, monkeypatch):
    scanner = BoundedScanner(concurrency=1, queue_size=0)
    marker = tmp_path / "child.pid"
    body = "while True: pass" if mode == "cpu" else "import time; time.sleep(30)"
    script = (
        "import os; from pathlib import Path; "
        f"Path({str(marker)!r}).write_text(str(os.getpid()));"
        "print('{\"ready\":true}',flush=True);\n" + body
    )
    monkeypatch.setattr(scanner, "command", lambda _: (sys._base_executable, "-c", script))
    limits = ScanLimits(cpu_ms=150 if mode == "cpu" else 2000, wall_ms=2000)
    task = asyncio.create_task(scanner.scan("data", limits))
    for _ in range(200):
        if marker.exists():
            break
        await asyncio.sleep(0.01)
    assert marker.exists()
    pid = int(marker.read_text())
    with pytest.raises(ScanUnavailable, match="artifact_scan_busy"):
        await scanner.scan("data", limits)
    if mode == "cancel":
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        with pytest.raises(
            ScanUnavailable, match="scan_cpu_limit" if mode == "cpu" else "scan_timeout"
        ):
            await task
    assert scanner._admitted == 0
    # A real process probe, rather than merely checking a mocked kill() call.
    from evoagent.privacy.scanner import process_cpu_seconds

    if sys.platform == "win32":
        import subprocess

        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
        )
        assert f'"{pid}"' not in result.stdout
    else:
        with pytest.raises(OSError):
            process_cpu_seconds(pid)


@pytest.mark.asyncio
async def test_queue_deadline_and_size_rejection(monkeypatch):
    scanner = BoundedScanner(concurrency=1, queue_size=1)
    await scanner._slots.acquire()
    try:
        with pytest.raises(ScanUnavailable, match="scan_timeout"):
            await scanner.scan("data", ScanLimits(wall_ms=20))
        with pytest.raises(ScanUnavailable, match="scan_budget_exceeded"):
            await scanner.scan("long", ScanLimits(max_bytes=2))
        assert scanner._admitted == 0
    finally:
        scanner._slots.release()
