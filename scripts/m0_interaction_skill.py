"""计划 §5.1 要求的两类 dev 任务：运行中介入与 Skill 对照。

- **运行中介入**：在模型/工具边界注入一条补充约束，验证"注入生效、已执行动作保留"；
- **Skill 对照**：同一任务跑 control / treatment 两臂，验证**配对机制与报告字段**，
  并明确声明它不构成 Skill 有效性证据（那属于 M6，需要未暴露 holdout 与样本量预注册）。

用法：
    python scripts/m0_interaction_skill.py [--dataset PATH] [--output PATH]
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

from evoagent.core.context import ContextBuilder
from evoagent.core.events import InMemoryEventSink
from evoagent.core.loop import AgentLoop
from evoagent.core.models import (
    AgentLoopStatus,
    EventType,
    FinishReason,
    Message,
    MessageRole,
    ModelResponse,
    ToolCall,
)
from evoagent.projects.context import build_project_context
from evoagent.projects.schema import ProjectAuthorization
from evoagent.projects.service import ActiveProject
from evoagent.providers.mock import MockProvider
from evoagent.tools.builtin.find_files import FindFilesTool
from evoagent.tools.builtin.list_dir import ListDirTool
from evoagent.tools.builtin.project_file_read import ProjectFileReadTool
from evoagent.tools.builtin.search_text import SearchTextTool
from evoagent.tools.executor import ToolExecutor
from evoagent.tools.registry import ToolRegistry

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET = ROOT / "evals/datasets/m0-interaction-skill-dev-v1.json"


def load_dataset(path: Path) -> tuple[dict, str]:
    raw = path.read_bytes()
    dataset = json.loads(raw)
    if dataset.get("schema_version") != 1 or dataset.get("set") != "dev":
        raise ValueError("M0 dev dataset must use schema_version 1 and set=dev")
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


def tool_registry(root: Path) -> ToolRegistry:
    return ToolRegistry(
        [ListDirTool(root), FindFilesTool(root), SearchTextTool(root), ProjectFileReadTool(root)]
    )


def scripted_provider(case: dict) -> MockProvider:
    steps: list[ModelResponse] = []
    for index, step in enumerate(case["steps"]):
        if step["type"] == "tool":
            steps.append(
                ModelResponse(
                    message=Message(
                        role=MessageRole.ASSISTANT,
                        tool_calls=(
                            ToolCall(
                                call_id=f"{case['id']}-{index}",
                                name=step["tool"],
                                arguments=step["arguments"],
                            ),
                        ),
                    ),
                    finish_reason=FinishReason.TOOL_CALLS,
                )
            )
        else:
            steps.append(
                ModelResponse(
                    message=Message(role=MessageRole.ASSISTANT, content=step["content"]),
                    finish_reason=FinishReason.STOP,
                )
            )
    return MockProvider(steps)


def instruction_source(case: dict):
    """按 `inject_before_step` 在指定迭代前交付一次指令，之后不再返回。"""

    pending = case["instruction"]
    state = {"iteration": 0, "delivered": 0}

    async def provider() -> tuple[str, ...]:
        state["iteration"] += 1
        if state["delivered"] or state["iteration"] < pending["inject_before_step"]:
            return ()
        state["delivered"] += 1
        return (pending["content"],)

    return provider


async def run_arm(case: dict, root: Path, *, arm: str) -> dict:
    project = ActiveProject(
        id=uuid4(),
        name=root.name,
        root=root,
        authorization=ProjectAuthorization.READ,
        authorization_version=1,
    )
    registry = tool_registry(root)
    sink = InMemoryEventSink(uuid4())
    executor = ToolExecutor(registry, sink, timeout_seconds=30, max_result_chars=20_000)
    provider = scripted_provider(case)
    loop = AgentLoop(
        provider,
        registry,
        executor,
        sink,
        model="mock-model",
        max_iterations=12,
        max_total_tokens=100_000,
        instruction_provider=(
            instruction_source(case) if case.get("instruction") is not None else None
        ),
    )
    messages = ContextBuilder().build(
        case["goal"],
        project_context=build_project_context(project),
        skill_context=case.get("skill_text") if arm == "treatment" else None,
    )
    result = await loop.run(messages)

    tool_started = [
        event.payload.get("name")
        for event in sink.events
        if event.type is EventType.TOOL_STARTED and event.payload
    ]
    tool_completed = [
        event.payload.get("name")
        for event in sink.events
        if event.type is EventType.TOOL_COMPLETED and event.payload
    ]
    injected = [
        event.payload
        for event in sink.events
        if event.type is EventType.INSTRUCTION_INJECTED and event.payload is not None
    ]
    answer = result.final_answer or ""
    anchors = case.get("answer_anchors", [])
    return {
        "arm": arm,
        "loop_status": result.status.value,
        "model_requests": len(provider.requests),
        "tool_started": tool_started,
        "tool_completed": tool_completed,
        "instructions_injected": len(injected),
        "answer_anchors": [{"anchor": item, "found": item in answer} for item in anchors],
        "answer_chars": len(answer),
        "total_tokens": result.usage.total_tokens if result.usage is not None else 0,
        "skill_injected": arm == "treatment",
    }


async def run_case(case: dict, root: Path) -> dict:
    if case["kind"] == "interaction":
        outcome = await run_arm(case, root, arm="control")
        expected_injected = case.get("expect_instruction_injected", 1)
        preserved = case.get("expect_executed_actions_preserved", [])
        checks = [
            {
                "name": "instruction_injected_once",
                "passed": outcome["instructions_injected"] == expected_injected,
                "actual": outcome["instructions_injected"],
            },
            {
                "name": "executed_actions_preserved",
                # 注入不得撤销已经执行的工具动作：先前工具仍出现在 started/completed 序列里。
                "passed": all(item in outcome["tool_completed"] for item in preserved),
                "actual": outcome["tool_completed"],
            },
            {
                "name": "anchors_present",
                "passed": all(item["found"] for item in outcome["answer_anchors"]),
            },
            {
                "name": "loop_completed",
                "passed": outcome["loop_status"] == AgentLoopStatus.COMPLETED.value,
            },
        ]
        return {"id": case["id"], "kind": case["kind"], "checks": checks, "outcome": outcome}

    # Skill 对照：两臂各跑一遍，报告字段必须齐全且可比较。
    control = await run_arm(case, root, arm="control")
    treatment = await run_arm(case, root, arm="treatment")
    comparable = (
        control["model_requests"] == treatment["model_requests"]
        and control["tool_started"] == treatment["tool_started"]
    )
    checks = [
        {
            "name": "both_arms_completed",
            "passed": control["loop_status"] == treatment["loop_status"] == "completed",
        },
        {
            "name": "arms_comparable",
            "passed": comparable,
            "detail": [control["tool_started"], treatment["tool_started"]],
        },
        {
            "name": "skill_injected_only_in_treatment",
            "passed": treatment["skill_injected"] and not control["skill_injected"],
        },
        {
            "name": "anchors_present_in_both",
            "passed": all(
                item["found"] for item in control["answer_anchors"] + treatment["answer_anchors"]
            ),
        },
    ]
    return {
        "id": case["id"],
        "kind": case["kind"],
        "checks": checks,
        "arms": {"control": control, "treatment": treatment},
        # 明确写下这条用例能证明什么、不能证明什么。
        "evidence_scope": "pairing machinery only; NOT evidence of Skill benefit (that is M6)",
    }


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--output", type=Path, default=ROOT / "output/m0-interaction-skill.json")
    args = parser.parse_args()

    dataset, dataset_sha = load_dataset(args.dataset)
    source = (ROOT / dataset["fixture"]).resolve()
    if not source.is_relative_to((ROOT / "evals/fixtures").resolve()) or not source.is_dir():
        parser.error("fixture must be an existing directory under evals/fixtures")

    with tempfile.TemporaryDirectory(prefix="evoagent-m0-") as directory:
        fixture = Path(directory) / "project"
        revision = create_git_fixture(source, fixture)
        before = digest_tree(fixture)
        cases = [await run_case(case, fixture) for case in dataset["cases"]]
        report = {
            "schema_version": 1,
            "created_at": datetime.now(UTC).isoformat(),
            "mode": "mock_evidence_only",
            "scope": (
                "scripted mock tool sequence; proves instruction injection boundaries and "
                "pairing machinery, not model behaviour or Skill benefit"
            ),
            "agent_behaviour_verified": False,
            "skill_benefit_verified": False,
            "dataset_sha256": dataset_sha,
            "fixture_git_revision": revision,
            "fixture_before_sha256": before,
            "fixture_after_sha256": digest_tree(fixture),
            "cases": cases,
            "harness_passed": all(all(item["passed"] for item in case["checks"]) for case in cases),
        }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    passed = sum(1 for case in cases if all(item["passed"] for item in case["checks"]))
    print(f"{passed}/{len(cases)} harness_passed；输出 {args.output}")
    return 0 if report["harness_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
