"""Bounded per-event-loop admission and disposable, killable CPU scanning."""

import asyncio
import ctypes
import json
import os
import subprocess
import sys
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from time import monotonic
from weakref import WeakKeyDictionary

from evoagent.privacy.redaction import POLICY_VERSION, SENSITIVE_CATEGORIES, RedactionResult


@dataclass(frozen=True)
class ScanLimits:
    max_bytes: int = 8 * 1024 * 1024
    cpu_ms: int = 250
    wall_ms: int = 1000
    concurrency: int = 2
    queue_size: int = 16


class ScanUnavailable(ValueError):
    def __init__(self, reason):
        self.reason = reason
        super().__init__(reason)


def process_cpu_seconds(pid: int) -> float:
    if sys.platform == "win32":
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.GetProcessTimes.argtypes = (wintypes.HANDLE,) + (
            ctypes.POINTER(wintypes.FILETIME),
        ) * 4
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            raise OSError("scanner CPU accounting unavailable")
        times = [wintypes.FILETIME() for _ in range(4)]
        try:
            if not kernel.GetProcessTimes(handle, *(ctypes.byref(item) for item in times)):
                raise OSError("scanner CPU accounting unavailable")
            return sum((item.dwHighDateTime << 32) | item.dwLowDateTime for item in times[2:]) / 1e7
        finally:
            kernel.CloseHandle(handle)
    fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
    return (int(fields[11]) + int(fields[12])) / os.sysconf("SC_CLK_TCK")


class BoundedScanner:
    def __init__(self, *, concurrency=2, queue_size=16):
        self.concurrency = concurrency
        self.queue_size = queue_size
        self._slots = asyncio.Semaphore(concurrency)
        self._admitted = 0

    def command(self, limits):
        return (
            sys._base_executable,
            "-m",
            "evoagent.privacy.scan_worker",
            str(limits.max_bytes),
            str(limits.cpu_ms),
        )

    async def scan(self, text: str, limits: ScanLimits, *, deadline=None) -> RedactionResult:
        data = text.encode("utf-8")
        if len(data) > limits.max_bytes:
            raise ScanUnavailable("scan_budget_exceeded")
        if self._admitted >= self.concurrency + self.queue_size:
            raise ScanUnavailable("artifact_scan_busy")
        deadline = deadline if deadline is not None else monotonic() + limits.wall_ms / 1000
        self._admitted += 1
        process = communication = None
        acquired = False
        try:
            async with asyncio.timeout_at(deadline):
                await self._slots.acquire()
                acquired = True
                spawning = asyncio.create_task(
                    asyncio.create_subprocess_exec(
                        *self.command(limits),
                        env={**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)},
                        stdin=asyncio.subprocess.PIPE,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.DEVNULL,
                        **(
                            {"creationflags": subprocess.CREATE_NO_WINDOW}
                            if sys.platform == "win32"
                            else {}
                        ),
                    )
                )
                try:
                    process = await asyncio.shield(spawning)
                except asyncio.CancelledError:
                    # Creation itself may finish after cancellation. Take ownership
                    # of its child before propagating cancellation to finally.
                    process = await spawning
                    raise
                ready = await process.stdout.readline()
                if ready.strip() != b'{"ready":true}':
                    raise ScanUnavailable("scan_failed")
                cpu_started = process_cpu_seconds(process.pid)
                communication = asyncio.create_task(process.communicate(data))
                while not communication.done():
                    try:
                        cpu = process_cpu_seconds(process.pid)
                    except OSError:
                        await asyncio.sleep(0)
                        if process.returncode is None and not communication.done():
                            raise ScanUnavailable("scan_cpu_unavailable") from None
                    else:
                        if (cpu - cpu_started) * 1000 > limits.cpu_ms:
                            raise ScanUnavailable("scan_cpu_limit")
                    await asyncio.wait((communication,), timeout=0.01)
                output, _ = await communication
                if process.returncode != 0 or len(output) > 1024:
                    raise ScanUnavailable("scan_failed")
                payload = json.loads(output)
                if payload["cpu_ms"] > limits.cpu_ms:
                    raise ScanUnavailable("scan_cpu_limit")
                categories = payload["categories"]
                if not isinstance(categories, list) or any(
                    item not in SENSITIVE_CATEGORIES for item in categories
                ):
                    raise ScanUnavailable("scan_failed")
                return RedactionResult(
                    text="", categories=tuple(categories), policy_version=POLICY_VERSION
                )
        except TimeoutError:
            raise ScanUnavailable("scan_timeout") from None
        except (OSError, ValueError, KeyError) as error:
            if isinstance(error, ScanUnavailable):
                raise
            raise ScanUnavailable("scan_failed") from None
        finally:
            # Never release the slot while a timed-out scanner is still running.
            try:
                if process is not None:
                    if process.returncode is None:
                        with suppress(ProcessLookupError):
                            process.kill()
                    cleanup = asyncio.create_task(process.wait())
                    try:
                        await asyncio.shield(cleanup)
                    except asyncio.CancelledError:
                        await cleanup
                        raise
                    finally:
                        if communication is not None:
                            if not communication.done():
                                communication.cancel()
                            await asyncio.gather(communication, return_exceptions=True)
            finally:
                if acquired:
                    self._slots.release()
                self._admitted -= 1


_SCANNERS = WeakKeyDictionary()


def shared_scanner(limits: ScanLimits) -> BoundedScanner:
    loop = asyncio.get_running_loop()
    # One admission budget per Worker loop, shared by all injection readers.
    scanner = _SCANNERS.get(loop)
    if scanner is None:
        scanner = BoundedScanner(concurrency=limits.concurrency, queue_size=limits.queue_size)
        _SCANNERS[loop] = scanner
    elif (scanner.concurrency, scanner.queue_size) != (limits.concurrency, limits.queue_size):
        raise ScanUnavailable("scan_configuration_mismatch")
    return scanner
