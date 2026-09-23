"""可重复的四条验收演示；运行真实服务代码，报告保留失败和跳过。"""

import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEMOS = {
    "context": [
        "tests/integration/test_phase4_demos.py::test_demo_context_restore_and_block",
        "tests/integration/test_memory_foundations.py::test_context_failure_before_commit_keeps_old_snapshot",
        "tests/unit/test_context_policy.py::test_rejected_request_never_reaches_provider_and_legacy_can_run",
    ],
    "memory": [
        "tests/integration/test_phase4_demos.py::test_demo_cross_session_memory_and_revoke",
        "tests/integration/test_memory_foundations.py::test_erase_failure_keeps_job_failed_and_retry_finishes_cleanup",
    ],
    "mcp": [
        "tests/integration/test_phase4_demos.py::test_demo_mcp_call_change_and_unload",
        "tests/integration/test_mcp_catalogs.py::test_real_http_sdk_and_api_discovery",
        "tests/integration/test_mcp_execution.py::test_write_uncertain_is_unknown_and_never_replayed",
    ],
    "workers": [
        "tests/integration/test_multiworker.py::test_two_real_worker_processes_same_label",
        "tests/e2e/test_worker_process_recovery.py::test_api_task_survives_killed_worker",
        "tests/integration/test_phase4_demos.py::test_demo_real_redis_outage_keeps_db_task",
        "tests/integration/test_worker_redis.py::test_real_redis_atomic_bucket_and_duplicate_wakeup",
    ],
}


def read_cases(path):
    cases = []
    for case in ET.parse(path).iter("testcase"):
        state = "passed"
        for name in ("skipped", "failure", "error"):
            if case.find(name) is not None:
                state = name
        evidence = {}
        for prop in case.findall("./properties/property"):
            if prop.attrib["name"] == "evidence":
                evidence = json.loads(prop.attrib["value"])
        cases.append({"name": case.attrib["name"], "status": state, "evidence": evidence})
    return cases


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--demo", choices=["all", *DEMOS], default="all")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    digest = hashlib.sha256()
    for directory in ("src", "tests", "scripts"):
        for path in sorted((ROOT / directory).rglob("*.py")):
            digest.update(path.relative_to(ROOT).as_posix().encode())
            digest.update(path.read_bytes())
    report = {
        "schema_version": 1,
        "created_at": datetime.now(UTC).isoformat(),
        "python": platform.python_version(),
        "platform": platform.system(),
        "source_sha256": digest.hexdigest(),
        "model": "deterministic fixtures; no paid model calls",
        "postgres_configured": bool(os.getenv("EVOAGENT_TEST_DATABASE_URL")),
        "redis_configured": bool(os.getenv("EVOAGENT_TEST_REDIS_URL")),
        "demos": {},
    }
    names = DEMOS if args.demo == "all" else [args.demo]
    with tempfile.TemporaryDirectory(prefix="evoagent-demo-") as temporary:
        for name in names:
            xml = Path(temporary) / f"{name}.xml"
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pytest",
                    "-q",
                    *DEMOS[name],
                    f"--junitxml={xml}",
                    "-o",
                    "junit_family=legacy",
                ],
                cwd=ROOT,
                check=False,
            )
            cases = read_cases(xml) if xml.exists() else []
            report["demos"][name] = {
                "exit_code": result.returncode,
                "status": "passed"
                if result.returncode == 0
                and cases
                and all(case["status"] == "passed" for case in cases)
                else "incomplete",
                "cases": cases,
            }
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(
                json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
    return 0 if all(d["status"] == "passed" for d in report["demos"].values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
