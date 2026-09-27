"""跑 M2 项目编辑 dev 集，收集编辑链路的**证据采集器层面**结果。

它证明的是：哈希前置条件、多文件全成功/全回滚、冲突检测与 diff 证据在真实文件系统上
按契约工作。它**不**证明 Agent 会自己定位要改的位置：MockProvider 的调用脚本是脚本化的。

用法：
    python scripts/m2_project_edit.py [--dataset PATH] [--output PATH]
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import shutil
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from evoagent.core.events import InMemoryEventSink
from evoagent.core.models import ToolCall, ToolResultStatus
from evoagent.projects.schema import ProjectAuthorization
from evoagent.tools.builtin.project_edit import project_edit_tools
from evoagent.tools.builtin.project_file_read import ProjectFileReadTool
from evoagent.tools.executor import ToolExecutor
from evoagent.tools.registry import ToolRegistry

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET = ROOT / "evals/datasets/m2-project-edit-dev-v1.json"


def digest_files(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        parts = path.relative_to(root).parts
        if path.is_file() and ".git" not in parts and "__pycache__" not in parts:
            digest.update(path.relative_to(root).as_posix().encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()


def load_dataset(path: Path) -> tuple[dict, str]:
    raw = path.read_bytes()
    dataset = json.loads(raw)
    if dataset.get("schema_version") != 1 or dataset.get("set") != "dev":
        raise ValueError("M2 requires a version 1 dev dataset")
    cases = dataset.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("dataset has no cases")
    return dataset, hashlib.sha256(raw).hexdigest()


def create_git_fixture(source: Path, target: Path) -> str:
    shutil.copytree(source, target)
    for command in (
        ["git", "init", "-q"],
        ["git", "config", "user.email", "dev@example.invalid"],
        ["git", "config", "user.name", "EvoAgent Dev"],
        ["git", "add", "-A"],
        ["git", "commit", "-q", "-m", "fixture"],
    ):
        subprocess.run(command, cwd=target, check=True)
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=target, check=True, capture_output=True, text=True
    ).stdout.strip()


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def substitute(arguments: dict, *, root: Path, read_sha: str | None) -> dict:
    """把 `$read_sha` 占位符换成上一步 file_read 的哈希。"""

    resolved = json.loads(json.dumps(arguments, ensure_ascii=False))
    for key, value in list(resolved.items()):
        if value == "$read_sha":
            if read_sha is None:
                raise ValueError("$read_sha 需要先执行一次 file_read")
            resolved[key] = read_sha
    return resolved


async def collect_case(case: dict, root: Path) -> dict:
    registry = ToolRegistry(
        [
            ProjectFileReadTool(root),
            *project_edit_tools(root, authorization=ProjectAuthorization.READ_WRITE),
        ]
    )
    sink = InMemoryEventSink(uuid4())
    executor = ToolExecutor(registry, sink, timeout_seconds=30, max_result_chars=50_000)

    steps = case["steps"]
    interference = case.get("interfere_before_last_tool")
    read_sha: str | None = None
    results: list[dict] = []
    for index, step in enumerate(steps):
        if interference is not None and index == len(steps) - 1:
            target = root / interference["path"]
            target.write_text(
                target.read_text(encoding="utf-8") + interference["append"], encoding="utf-8"
            )
        arguments = substitute(step["arguments"], root=root, read_sha=read_sha)
        result = await executor.execute(
            ToolCall(call_id=f"{case['id']}-{index}", name=step["tool"], arguments=arguments)
        )
        results.append(
            {
                "tool": step["tool"],
                "status": result.status.value,
                "error_code": result.error_code,
                "content": result.content,
            }
        )
        if step["tool"] == "file_read" and result.status is ToolResultStatus.SUCCESS:
            read_sha = file_sha256(root / arguments["path"])

    expected_failure = case.get("expect_tool_failure")
    failed = [item for item in results if item["status"] != ToolResultStatus.SUCCESS.value]
    checks: list[dict] = []
    if expected_failure is not None:
        checks.append(
            {
                "name": "expected_failure_observed",
                "passed": any(
                    item["tool"] == expected_failure
                    and item["error_code"] == case["expect_error_code"]
                    for item in failed
                ),
            }
        )
        checks.append(
            {
                "name": "no_unexpected_failure",
                "passed": all(item["tool"] == expected_failure for item in failed),
            }
        )
    else:
        checks.append({"name": "no_failure", "passed": not failed})

    for relative in case.get("expect_applied", []):
        path = root / relative
        checks.append(
            {
                "name": f"applied:{relative}",
                "passed": path.is_file() and "def " in path.read_text(encoding="utf-8"),
            }
        )
    diff_text = "\n".join(item["content"] for item in results)
    for fragment in case.get("expect_diff_contains", []):
        checks.append({"name": f"diff:{fragment}", "passed": fragment in diff_text})
    for fragment in case.get("expect_result_contains", []):
        checks.append({"name": f"result:{fragment}", "passed": fragment in diff_text})
    for fragment in case.get("expect_unchanged_contains", []):
        content = "\n".join(
            (root / relative).read_text(encoding="utf-8")
            for relative in case.get("expect_applied", []) or _touched_paths(case)
            if (root / relative).is_file()
        )
        checks.append({"name": f"unchanged:{fragment}", "passed": fragment in content})

    return {
        "id": case["id"],
        "harness_passed": all(item["passed"] for item in checks),
        "checks": checks,
        "tool_results": [
            {
                **{key: value for key, value in item.items() if key != "content"},
                "content_chars": len(item["content"]),
            }
            for item in results
        ],
    }


def _touched_paths(case: dict) -> list[str]:
    paths: list[str] = []
    for step in case["steps"]:
        arguments = step["arguments"]
        if "path" in arguments:
            paths.append(arguments["path"])
        for edit in arguments.get("edits", []) if isinstance(arguments, dict) else []:
            paths.append(edit["path"])
    return sorted(set(paths))


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--output", type=Path, default=ROOT / "output/m2-project-edit.json")
    args = parser.parse_args()

    dataset, dataset_sha = load_dataset(args.dataset)
    source = (ROOT / dataset["fixture"]).resolve()
    if not source.is_relative_to((ROOT / "evals/fixtures").resolve()) or not source.is_dir():
        parser.error("fixture must be an existing directory under evals/fixtures")

    with tempfile.TemporaryDirectory(prefix="evoagent-m2-") as directory:
        fixture = Path(directory) / "project"
        revision = create_git_fixture(source, fixture)
        before = digest_files(fixture)
        cases = [await collect_case(case, fixture) for case in dataset["cases"]]
        report = {
            "schema_version": 1,
            "created_at": datetime.now(UTC).isoformat(),
            "mode": "mock_evidence_only",
            "scope": "scripted mock edits only; not a check of locating the right change",
            "agent_edit_planning_verified": False,
            "dataset_sha256": dataset_sha,
            "fixture_before_sha256": before,
            "fixture_after_sha256": digest_files(fixture),
            "fixture_git_revision": revision,
            "cases": cases,
            "harness_passed": all(case["harness_passed"] for case in cases),
        }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        f"{sum(1 for case in cases if case['harness_passed'])}/{len(cases)} harness_passed；"
        f"输出 {args.output}"
    )
    return 0 if report["harness_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
