"""Only immutable detection metadata is reused; authority remains a reader duty."""

from dataclasses import replace
from time import monotonic

import pytest

from evoagent.privacy.redaction import POLICY_VERSION, RedactionResult
from evoagent.privacy.scanner import BoundedScanner, ScanLimits, ScanUnavailable


async def test_actual_safe_scan_reuses_one_child_and_never_keeps_plaintext(monkeypatch):
    scanner = BoundedScanner()
    commands = []
    original = scanner.command

    def count(limits):
        commands.append(limits)
        return original(limits)

    monkeypatch.setattr(scanner, "command", count)
    limits = ScanLimits()
    one = await scanner.scan_identical("public fixture body", limits)
    two = await scanner.scan_identical("public fixture body", limits)
    assert one == two and len(commands) == 1
    assert all(result.text == "" for result in scanner._safe_results.values())
    await scanner.scan("public fixture body", limits)
    assert len(commands) == 2  # optimization off invokes the old scanner
    await scanner.scan_identical("another public body", limits)
    await scanner.scan_identical("public fixture body", replace(limits, cpu_ms=249))
    assert len(commands) == 4


async def test_sensitive_failure_and_policy_change_never_reuse_success(monkeypatch):
    import evoagent.privacy.scanner as module

    scanner = BoundedScanner()
    calls = []

    async def scan(text, limits, *, deadline=None):
        calls.append(text)
        if text == "unavailable":
            raise ScanUnavailable("scan_failed")
        return RedactionResult(
            text="",
            categories=("jwt",) if text == "sensitive" else (),
            policy_version=module.POLICY_VERSION,
        )

    monkeypatch.setattr(scanner, "scan", scan)
    for _ in range(2):
        await scanner.scan_identical("sensitive", ScanLimits())
        with pytest.raises(ScanUnavailable):
            await scanner.scan_identical("unavailable", ScanLimits())
    assert len(calls) == 4 and not scanner._safe_results
    await scanner.scan_identical("safe", ScanLimits())
    monkeypatch.setattr(module, "POLICY_VERSION", POLICY_VERSION + 1)
    await scanner.scan_identical("safe", ScanLimits())
    assert len(calls) == 6


async def test_size_deadline_and_lru_bound_apply_even_when_cached(monkeypatch):
    scanner = BoundedScanner()

    async def scan(text, limits, *, deadline=None):
        return RedactionResult(text="", categories=(), policy_version=POLICY_VERSION)

    monkeypatch.setattr(scanner, "scan", scan)
    limits = ScanLimits()
    for index in range(257):
        await scanner.scan_identical(f"public {index}", limits)
    assert len(scanner._safe_results) == 256
    with pytest.raises(ScanUnavailable, match="scan_timeout"):
        await scanner.scan_identical("public 256", limits, deadline=monotonic() - 1)
    with pytest.raises(ScanUnavailable, match="scan_budget_exceeded"):
        await scanner.scan_identical("public 256", replace(limits, max_bytes=1))
