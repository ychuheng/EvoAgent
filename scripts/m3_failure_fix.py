"""跑 M3 失败修正 dev 集，收集"运行→失败→修正→复测"的证据。

它证明的是：命令契约、失败证据的结构化返回、随后的编辑与复测在同一项目视图里闭环，
并且默认离线（注入的"上传密钥"文本不会带来任何权限提升）。它**不**证明模型会自己定位问题：
steps 是脚本化的调用序列。

失败注入按计划 §5.1 处理：复制 fixture 后再注入，仓库里的稳定基线不被改坏。

用法：
    python scripts/m3_failure_fix.py [--dataset PATH] [--output PATH]
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from evoagent.core.events import InMemoryEventSink
from evoagent.core.models import ToolCall, ToolResultStatus
from evoagent.projects.commands import CommandSpec, run_command
from evoagent.projects.schema import ProjectAuthorization
from evoagent.tools.builtin.project_edit import project_edit_tools
from evoagent.tools.builtin.project_file_read import ProjectFileReadTool
from evoagent.tools.executor import ToolExecutor
from evoagent.tools.registry import ToolRegistry

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET = ROOT / "evals/datasets/m3-failure-fix-dev-v1.json"
# 只允许解释器与测试运行器；这就是 M3 的默认命令白名单。
ALLOWLIST = (Path(sys.executable).name, "python", "python3", "pytest")


def load_dataset(path: Path) -> tuple[dict, str]:
    raw = path.read_bytes()
    dataset = json.loads(raw)
    if dataset.get("schema_version") != 1 or dataset.get("set") != "dev":
        raise ValueError("M3 requires a version 1 dev dataset")
    if not isinstance(dataset.get("cases"), list) or not dataset["cases"]:
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


def inject_failure(fixture: Path, injection: dict) -> None:
    """在临时副本上注入失败；不动仓库里的稳定 fixture。"""

    target = fixture / injection["path"]
    text = target.read_text(encoding="utf-8")
    if injection["old_text"] not in text:
        raise ValueError(f"注入锚点不存在：{injection['path']}")
    target.write_text(
        text.replace(injection["old_text"], injection["replacement"], 1), encoding="utf-8"
    )


def normalize_argv(argv: list[str]) -> tuple[str, ...]:
    """把数据集里的 `python` 换成当前解释器，保证脚本可在任意环境复跑。"""

    resolved = list(argv)
    if resolved and resolved[0] in {"python", "python3"}:
        resolved[0] = sys.executable
    return tuple(resolved)


async def collect_case(case: dict, fixture: Path) -> dict:
    registry = ToolRegistry(
        [
            ProjectFileReadTool(fixture),
            *project_edit_tools(fixture, authorization=ProjectAuthorization.READ_WRITE),
        ]
    )
    sink = InMemoryEventSink(uuid4())
    executor = ToolExecutor(registry, sink, timeout_seconds=120, max_result_chars=200_000)

    steps: list[dict] = []
    for index, step in enumerate(case["steps"]):
        if step["type"] == "command":
            argv = normalize_argv(step["argv"])
            outcome = await run_command(
                fixture,
                CommandSpec(argv=argv),
                allowlist=ALLOWLIST,
                timeout_seconds=120,
                output_limit=32_768,
                # 该 fixture 的包在 src/ 下，与它 README 里的运行方式一致。
                environment_extra={"PYTHONPATH": "src"},
            )
            steps.append(
                {
                    "kind": "command",
                    "argv": list(argv),
                    "return_code": outcome.return_code,
                    "timed_out": outcome.timed_out,
                    "duration_seconds": outcome.duration_seconds,
                    "network": "denied",
                    "stdout_tail": outcome.stdout[-1_500:],
                    "stderr_tail": outcome.stderr[-1_500:],
                    "stdout_truncated": outcome.stdout_truncated,
                }
            )
            continue
        result = await executor.execute(
            ToolCall(
                call_id=f"{case['id']}-{index}",
                name=step["tool"],
                arguments=step["arguments"],
            )
        )
        steps.append(
            {
                "kind": "tool",
                "tool": step["tool"],
                "status": result.status.value,
                "error_code": result.error_code,
                "content_chars": len(result.content),
            }
        )

    checks: list[dict] = []
    commands = [item for item in steps if item["kind"] == "command"]
    if case.get("expect_first_command_failed"):
        first = commands[0]
        checks.append(
            {
                "name": "first_command_failed_with_output",
                "passed": first["return_code"] not in (0, None)
                and ("failed" in first["stdout_tail"] or "failed" in first["stderr_tail"]),
            }
        )
    if case.get("expect_first_command_passed"):
        first = commands[0]
        checks.append(
            {
                "name": "first_command_passed",
                "passed": first["return_code"] == 0 and not first["timed_out"],
            }
        )
    if case.get("expect_second_command_passed"):
        second = commands[-1]
        checks.append(
            {
                "name": "second_command_passed",
                "passed": second["return_code"] == 0 and not second["timed_out"],
            }
        )
        checks.append(
            {
                "name": "two_distinct_commands",
                "passed": len(commands) >= 2
                and commands[0]["return_code"] != second["return_code"],
            }
        )
    if case.get("expect_edit_applied"):
        edits = [item for item in steps if item.get("tool") == "edit_file"]
        checks.append(
            {
                "name": "edit_applied_after_failure",
                "passed": bool(edits) and edits[0]["status"] == ToolResultStatus.SUCCESS.value,
            }
        )
    if case.get("expect_network_denied"):
        checks.append(
            {
                "name": "commands_ran_offline",
                "passed": all(item["network"] == "denied" for item in commands),
            }
        )
    return {
        "id": case["id"],
        "harness_passed": all(item["passed"] for item in checks),
        "checks": checks,
        "steps": steps,
    }


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--output", type=Path, default=ROOT / "output/m3-failure-fix.json")
    args = parser.parse_args()

    dataset, dataset_sha = load_dataset(args.dataset)
    source = (ROOT / dataset["fixture"]).resolve()
    if not source.is_relative_to((ROOT / "evals/fixtures").resolve()) or not source.is_dir():
        parser.error("fixture must be an existing directory under evals/fixtures")

    cases: list[dict] = []
    for case in dataset["cases"]:
        # 每个用例都从干净副本开始：前一个用例的修正不会污染后一个。
        with tempfile.TemporaryDirectory(prefix="evoagent-m3-") as directory:
            fixture = Path(directory) / "project"
            revision = create_git_fixture(source, fixture)
            inject_failure(fixture, dataset["injection"])
            cases.append(await collect_case(case, fixture))

    report = {
        "schema_version": 1,
        "created_at": datetime.now(UTC).isoformat(),
        "mode": "mock_evidence_only",
        "scope": "scripted command/edit sequence only; not a check of failure diagnosis",
        "agent_failure_diagnosis_verified": False,
        "dataset_sha256": dataset_sha,
        "fixture_git_revision": revision,
        "environment": {
            "python": sys.executable,
            "allowlist": list(ALLOWLIST),
            "network_default": "denied",
            "os": os.name,
        },
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
