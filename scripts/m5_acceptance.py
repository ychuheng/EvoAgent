"""M5 整体验收采集器：本机授权目录整理 + 失败样本留证。

计划 §10 验收要求"一例公开资料调研和一例本机授权目录整理均在网页完成
输入→处理→核对→取回"，并点名若干失败样本。这里覆盖**本机目录整理**与失败样本两部分：

- `cases`：在临时副本上按脚本化序列跑"列目录 → 出计划 → 核对冲突 → 执行 → 逐条核对去向"，
  最后按 `expect_executed` / `expect_untouched` / `expect_plan_conflicts` 核对**磁盘实际状态**；
- `failure_cases`：重复文件名、损坏 PDF、扫描 PDF、未声明编码、越权路径、输入变化，
  每个都必须给出稳定错误码或明确冲突。

公开资料调研那一例由 `scripts/personal_research_eval.py` 负责（需要真实模型与搜索模式），
本脚本不重复实现，也不在没有真实模型时伪造调研结论。

用法：
    python scripts/m5_acceptance.py [--dataset PATH] [--output PATH]
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from evoagent.core.events import InMemoryEventSink
from evoagent.core.models import ToolCall
from evoagent.projects.inputs import InputChangedError, freeze_inputs, verify_inputs
from evoagent.projects.schema import ProjectAuthorization
from evoagent.tools.base import ToolExecutionError, ToolPermissionError
from evoagent.tools.builtin.list_dir import ListDirTool
from evoagent.tools.builtin.project_extract import ExtractTextArguments, ExtractTextTool
from evoagent.tools.builtin.project_organize import OrganizeFilesTool
from evoagent.tools.executor import ToolExecutor
from evoagent.tools.registry import ToolRegistry

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET = ROOT / "evals/datasets/m5-organize-acceptance-v1.json"


def load_dataset(path: Path) -> tuple[dict, str]:
    raw = path.read_bytes()
    dataset = json.loads(raw)
    if dataset.get("schema_version") != 1:
        raise ValueError("M5 acceptance dataset must use schema_version 1")
    if not dataset.get("cases"):
        raise ValueError("dataset has no cases")
    return dataset, hashlib.sha256(raw).hexdigest()


def digest_tree(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        parts = path.relative_to(root).parts
        if ".git" in parts or "__pycache__" in parts:
            continue
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def registry(root: Path) -> ToolRegistry:
    return ToolRegistry(
        [
            ListDirTool(root),
            ExtractTextTool(root),
            OrganizeFilesTool(root, authorization=ProjectAuthorization.READ_WRITE),
        ]
    )


async def run_case(case: dict, root: Path) -> dict:
    executor = ToolExecutor(
        registry(root), InMemoryEventSink(uuid4()), timeout_seconds=30, max_result_chars=200_000
    )
    results: list[dict] = []
    plan_payload: dict | None = None
    for index, step in enumerate(case["steps"]):
        result = await executor.execute(
            ToolCall(
                call_id=f"{case['id']}-{index}",
                name=step["tool"],
                arguments=step["arguments"],
            )
        )
        results.append(
            {
                "tool": step["tool"],
                "status": result.status.value,
                "error_code": result.error_code,
                "content": result.content,
            }
        )
        if step["tool"] == "organize_files":
            try:
                payload = json.loads(result.content)
            except json.JSONDecodeError:  # pragma: no cover - 工具总是返回 JSON
                continue
            if payload.get("dry_run"):
                plan_payload = payload

    checks: list[dict] = []
    if plan_payload is not None:
        # `plan.as_dict()` 里 conflicts/skipped 是**计数**，items 才是逐条清单。
        plan = plan_payload["plan"]
        conflict_items = [item for item in plan["items"] if item["status"] == "conflict"]
        checks.append(
            {
                "name": "plan_reports_conflicts",
                "passed": len(conflict_items) == case.get("expect_plan_conflicts", 0),
                "detail": [item["source"] for item in conflict_items],
            }
        )
        checks.append(
            {
                "name": "plan_counts_are_consistent",
                "passed": plan["conflicts"] == len(conflict_items)
                and plan["total"] == len(plan["items"]),
            }
        )
    else:
        checks.append({"name": "plan_reports_conflicts", "passed": False, "detail": "没有生成计划"})

    applied = [item for item in results if item["tool"] == "organize_files"][-1]
    executed: list[list[str]] = []
    try:
        executed = [
            [item["source"], item["destination"]]
            for item in json.loads(applied["content"])["result"]["executed"]
        ]
    except (KeyError, json.JSONDecodeError, TypeError):  # pragma: no cover - 失败时如实为空
        executed = []
    checks.append(
        {
            "name": "executed_matches_expectation",
            # 两侧都归一成"排序后的路径对"，否则 list 与 tuple 比较会假失败。
            "passed": sorted([list(pair) for pair in executed])
            == sorted([list(pair) for pair in case["expect_executed"]]),
            "detail": executed,
        }
    )
    for relative in case.get("expect_untouched", []):
        checks.append(
            {
                "name": f"untouched:{relative}",
                "passed": (root / relative).is_file(),
            }
        )
    for source, destination in case["expect_executed"]:
        checks.append(
            {
                "name": f"moved:{source}->{destination}",
                "passed": (root / destination).is_file() and not (root / source).exists(),
            }
        )
    return {
        "id": case["id"],
        "harness_passed": all(item["passed"] for item in checks),
        "checks": checks,
        "tool_results": [
            {key: value for key, value in item.items() if key != "content"} for item in results
        ],
    }


async def run_failure_case(case: dict, root: Path) -> dict:
    kind = case["kind"]
    observed: dict[str, object] = {}

    if kind == "organize":
        tool = OrganizeFilesTool(root, authorization=ProjectAuthorization.READ_WRITE)
        from evoagent.tools.builtin.project_organize import OrganizeFilesArguments

        payload = json.loads(
            await tool.invoke(
                OrganizeFilesArguments(
                    rules=[
                        {"source": item["source"], "destination": item["destination"]}
                        for item in case["rules"]
                    ],
                    dry_run=True,
                )
            )
        )
        plan = payload["plan"]
        observed = {
            "conflicts": plan["conflicts"],
            "skipped": plan["skipped"],
            "ready": plan["ready"],
            "items": plan["items"],
        }
        if "expect_conflicts" in case:
            passed = plan["conflicts"] == case["expect_conflicts"]
        else:
            passed = plan["skipped"] == case["expect_skipped"]
        return {
            "id": case["id"],
            "harness_passed": bool(passed),
            "expected": case.get("expect_conflicts", case.get("expect_skipped")),
            "observed": observed,
        }

    if kind == "extract":
        tool = ExtractTextTool(root)
        code: str | None = None
        try:
            await tool.invoke(ExtractTextArguments(path=case["path"]))
        except (ToolExecutionError, ToolPermissionError) as error:
            match = str(error)
            for candidate in (
                "pdf_unreadable",
                "pdf_scanned_or_empty",
                "unsupported_text_encoding",
                "pdf_encrypted",
            ):
                if candidate in match:
                    code = candidate
                    break
            if code is None:
                code = "other"
        observed = {"error_code": code, "original_kept": (root / case["path"]).is_file()}
        return {
            "id": case["id"],
            "harness_passed": code == case["expect_error_code"] and bool(observed["original_kept"]),
            "expected": case["expect_error_code"],
            "observed": observed,
        }

    if kind == "input_change":
        frozen = await freeze_inputs(root, case["input_paths"])
        target = root / case["input_paths"][0]
        target.write_text("内容已被替换\n", encoding="utf-8")
        code = None
        try:
            verify_inputs(root, frozen)
        except InputChangedError as error:
            code = getattr(error, "code", "input_changed")
        return {
            "id": case["id"],
            "harness_passed": code == case["expect_error_code"],
            "expected": case["expect_error_code"],
            "observed": {"error_code": code},
        }

    raise ValueError(f"unknown failure kind: {kind}")


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--output", type=Path, default=ROOT / "output/m5-acceptance.json")
    args = parser.parse_args()

    dataset, dataset_sha = load_dataset(args.dataset)
    source = (ROOT / dataset["fixture"]).resolve()
    if not source.is_relative_to((ROOT / "evals/fixtures").resolve()) or not source.is_dir():
        parser.error("fixture must be an existing directory under evals/fixtures")

    cases: list[dict] = []
    failures: list[dict] = []
    with tempfile.TemporaryDirectory(prefix="evoagent-m5-") as directory:
        # 正常用例：每个用例一份干净副本，避免前一个用例的整理影响后一个。
        for case in dataset["cases"]:
            workspace = Path(directory) / f"case-{case['id']}"
            shutil.copytree(source, workspace)
            before = digest_tree(workspace)
            cases.append(await run_case(case, workspace))
            cases[-1]["fixture_before_sha256"] = before
            cases[-1]["fixture_after_sha256"] = digest_tree(workspace)

        # 失败样本：同样各用一份副本（输入变化用例会改文件内容）。
        for case in dataset["failure_cases"]:
            workspace = Path(directory) / f"failure-{case['id']}"
            shutil.copytree(source, workspace)
            failures.append(await run_failure_case(case, workspace))

    report = {
        "schema_version": 1,
        "created_at": datetime.now(UTC).isoformat(),
        "mode": "mock_evidence_only",
        "scope": (
            "scripted tool sequence on a temporary copy; proves the organizing chain and the "
            "failure codes, not that a model picks the right classification"
        ),
        "agent_classification_verified": False,
        "dataset_sha256": dataset_sha,
        "fixture": dataset["fixture"],
        "cases": cases,
        "failure_cases": failures,
        "harness_passed": all(item["harness_passed"] for item in cases)
        and all(item["harness_passed"] for item in failures),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    ok = sum(1 for item in cases + failures if item["harness_passed"])
    print(f"{ok}/{len(cases) + len(failures)} harness_passed；输出 {args.output}")
    return 0 if report["harness_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
