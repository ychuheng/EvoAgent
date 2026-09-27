"""Collect Mock tool evidence for the M0a project-reading development set."""

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
    FinishReason,
    Message,
    MessageRole,
    ModelResponse,
    ToolCall,
)
from evoagent.providers.mock import MockProvider
from evoagent.tools.builtin.file_read import FileReadTool
from evoagent.tools.executor import ToolExecutor
from evoagent.tools.registry import ToolRegistry

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET = ROOT / "evals/datasets/m0a-project-dev-v1.json"


def digest_files(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if path.is_file() and ".git" not in path.relative_to(root).parts:
            digest.update(path.relative_to(root).as_posix().encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()


def load_dataset(path: Path) -> tuple[dict, str]:
    raw = path.read_bytes()
    dataset = json.loads(raw)
    if dataset.get("schema_version") != 1 or dataset.get("set") != "dev":
        raise ValueError("M0a requires a version 1 dev dataset")
    cases = dataset.get("cases")
    if not isinstance(cases, list) or len(cases) < 3:
        raise ValueError("M0a requires at least three development cases")
    identifiers = [case.get("id") for case in cases]
    if any(not isinstance(item, str) or not item for item in identifiers):
        raise ValueError("case IDs must be nonempty strings")
    if len(set(identifiers)) != len(identifiers):
        raise ValueError("case IDs must be unique")
    for case in cases:
        if not isinstance(case.get("goal"), str) or not case["goal"].strip():
            raise ValueError(f"{case['id']}: goal is missing")
        if not isinstance(case.get("reads"), list) or not case["reads"]:
            raise ValueError(f"{case['id']}: file reads are missing")
        for read in case["reads"]:
            if not isinstance(read.get("path"), str) or not isinstance(read.get("contains"), str):
                raise ValueError(f"{case['id']}: invalid read expectation")
            if not read["path"] or not read["contains"]:
                raise ValueError(f"{case['id']}: read expectation cannot be empty")
    return dataset, hashlib.sha256(raw).hexdigest()


def create_git_fixture(source: Path, destination: Path) -> str:
    shutil.copytree(source, destination)
    commands = (
        ["git", "init", "-q"],
        ["git", "add", "--all"],
        [
            "git",
            "-c",
            "user.name=EvoAgent Fixture",
            "-c",
            "user.email=fixture@localhost.invalid",
            "commit",
            "-qm",
            "M0a fixture baseline",
        ],
    )
    for command in commands:
        subprocess.run(command, cwd=destination, check=True, capture_output=True, text=True)
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=destination,
        check=True,
        capture_output=True,
        text=True,
    )
    return revision.stdout.strip()


async def collect_case(case: dict, fixture: Path) -> dict:
    calls = tuple(
        ToolCall(call_id=f"read-{index}", name="file_read", arguments={"path": item["path"]})
        for index, item in enumerate(case["reads"])
    )
    provider = MockProvider(
        [
            ModelResponse(
                message=Message(role=MessageRole.ASSISTANT, tool_calls=calls),
                finish_reason=FinishReason.TOOL_CALLS,
            ),
            ModelResponse(
                message=Message(
                    role=MessageRole.ASSISTANT,
                    content="Mock 已读取预设文件；这不是项目理解能力的证明。",
                ),
                finish_reason=FinishReason.STOP,
            ),
        ]
    )
    sink = InMemoryEventSink(uuid4())
    registry = ToolRegistry([FileReadTool(fixture)])
    executor = ToolExecutor(registry, sink, timeout_seconds=5, max_result_chars=20_000)
    loop = AgentLoop(
        provider,
        registry,
        executor,
        sink,
        model="m0a-mock",
        max_iterations=3,
        max_total_tokens=8_000,
    )
    result = await loop.run(ContextBuilder().build(case["goal"]))
    tool_messages = {
        message.tool_call_id: message.content
        for message in provider.requests[-1].messages
        if message.role is MessageRole.TOOL
    }
    reads = []
    for index, expectation in enumerate(case["reads"]):
        path = (fixture / expectation["path"]).resolve()
        inside = path.is_relative_to(fixture.resolve())
        content = tool_messages.get(f"read-{index}")
        reads.append(
            {
                "path": expectation["path"],
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest()
                if inside and path.is_file()
                else None,
                "observed": content is not None,
                "anchor_found": bool(content and expectation["contains"] in content),
            }
        )
    passed = (
        result.status is AgentLoopStatus.COMPLETED
        and len(provider.requests) == 2
        and all(item["observed"] and item["anchor_found"] for item in reads)
    )
    return {
        "id": case["id"],
        # harness_passed 只表示"脚本化 Mock 读取执行成功且锚点存在"，不是 Agent 答对。
        "harness_passed": passed,
        "loop_status": result.status.value,
        "model_requests": len(provider.requests),
        "event_types": [event.type.value for event in sink.events],
        "reads": reads,
    }


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--output", type=Path, default=ROOT / "output/m0a-project-dev.json")
    args = parser.parse_args()
    dataset, dataset_sha = load_dataset(args.dataset)
    source = (ROOT / dataset["fixture"]).resolve()
    if not source.is_relative_to((ROOT / "evals/fixtures").resolve()) or not source.is_dir():
        parser.error("fixture must be an existing directory under evals/fixtures")

    with tempfile.TemporaryDirectory(prefix="evoagent-m0a-") as directory:
        fixture = Path(directory) / "project"
        revision = create_git_fixture(source, fixture)
        cases = [await collect_case(case, fixture) for case in dataset["cases"]]
        report = {
            "schema_version": 1,
            "created_at": datetime.now(UTC).isoformat(),
            "mode": "mock_evidence_only",
            "scope": "scripted mock reads only; not a check of project understanding",
            "agent_project_understanding_verified": False,
            "dataset_sha256": dataset_sha,
            "fixture_sha256": digest_files(fixture),
            "fixture_git_revision": revision,
            "cases": cases,
            "harness_passed": all(case["harness_passed"] for case in cases),
        }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return 0 if report["harness_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
