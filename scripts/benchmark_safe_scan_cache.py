"""Measure only safe scan computation on fixed synthetic text; no DB or model IO."""

import argparse
import asyncio
import hashlib
import json
import math
import platform
from dataclasses import asdict
from pathlib import Path
from time import perf_counter

from evoagent.privacy.redaction import POLICY_VERSION
from evoagent.privacy.scanner import BoundedScanner, ScanLimits, ScanUnavailable

FIXTURE = "Public fixture sentence with no credential.\n" * 512


def percentile(values, fraction):
    return sorted(values)[max(0, math.ceil(len(values) * fraction) - 1)] if values else None


async def measure(repeats):
    limits = ScanLimits()
    cases = []
    for mode in ("disabled", "enabled_cold", "enabled_hot", "enabled_changed"):
        scanner = BoundedScanner()
        calls = 0
        original = scanner.scan

        async def counted(*args, _original=original, **kwargs):
            nonlocal calls
            calls += 1
            return await _original(*args, **kwargs)

        scanner.scan = counted
        if mode == "enabled_hot":
            await scanner.scan_identical(FIXTURE, limits)
            calls = 0
        timings, failures = [], []
        for index in range(repeats):
            if mode == "enabled_cold":
                scanner._safe_results.clear()  # benchmark-only explicit cold LRU
            text = FIXTURE + (f"\nSample {index}" if mode == "enabled_changed" else "")
            started = perf_counter()
            try:
                result = await (
                    scanner.scan(text, limits)
                    if mode == "disabled"
                    else scanner.scan_identical(text, limits)
                )
                if result.categories:
                    raise RuntimeError("fixed safe fixture unexpectedly matched sensitive rules")
                timings.append((perf_counter() - started) * 1000)
            except ScanUnavailable as error:
                failures.append(
                    {
                        "index": index,
                        "reason": error.reason,
                        "elapsed_ms": (perf_counter() - started) * 1000,
                    }
                )
        cases.append(
            {
                "mode": mode,
                "attempts": repeats,
                "passed": len(timings),
                "failures": failures,
                "scan_executions": calls,
                "p50_ms": percentile(timings, 0.5),
                "p95_ms": percentile(timings, 0.95),
                "max_ms": max(timings) if timings else None,
                "samples_ms": timings,
            }
        )
    return {
        "schema_version": 1,
        "python": platform.python_version(),
        "platform": platform.system(),
        "policy_version": POLICY_VERSION,
        "limits": asdict(limits),
        "fixture_sha256": hashlib.sha256(FIXTURE.encode()).hexdigest(),
        "fixture_bytes": len(FIXTURE.encode()),
        "scanner_module_sha256": hashlib.sha256(
            Path(
                __import__("evoagent.privacy.scanner", fromlist=["__file__"]).__file__
            ).read_bytes()
        ).hexdigest(),
        "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "cases": cases,
        "paid_calls": 0,
        "notes": [
            "Synthetic safe text only; no user artifacts or credentials were read",
            "Failures are listed separately and fail the CLI; P95 is successful-attempt latency",
            "Detector cost only; excludes DB, authorization, file reading and end-to-end latency",
            "Cache remains opt-in; these numbers do not establish total runtime benefit",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeats", type=int, default=30)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not 30 <= args.repeats <= 100:
        parser.error("repeats must be 30..100")
    report = asyncio.run(measure(args.repeats))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            [
                {key: item[key] for key in ("mode", "passed", "scan_executions", "p95_ms")}
                for item in report["cases"]
            ]
        )
    )
    if any(item["failures"] for item in report["cases"]):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
