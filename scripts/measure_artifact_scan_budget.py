"""测量工具输出归档的尺寸包络与敏感复查吞吐，作为 S0b 扫描预算的依据。

为什么需要（改造方案 §2.5）：M-A0 会让旧 artifact 在注入前做**整份正文**的敏感复查，
"资源上限"定多少必须有实测依据，不能凭空写一个数；§2.5 同时明确"缺报告不放行
M-A0 升级"。

本脚本给出两样东西：

1. **尺寸包络**：用真实写入路径 `ToolOutputStore.preserve()` 产生归档（归档条件见
   `max_tool_result_chars`，默认 20,000 字符），记录实际落盘字节；
2. **吞吐曲线**：对落盘正文逐个复扫，分别计 `detect_sensitive()` 与 `redact_text()`
   的耗时，得到"每 MB 多少毫秒"，据此换算扫描预算能覆盖多大正文。

**边界（不得越界解读）**：正文是**合成**的（日志 / 中文散文 / 代码 / 含密集假凭据），
不是真实任务的产物分布，因此本报告**不是** §2.5 要求的"验收库覆盖率报告"；
它只回答"扫描预算该定多大"，以及"归档正文的尺寸下限是多少"。

用法：
    python scripts/measure_artifact_scan_budget.py \
        --json docs/reports/artifact-scan-budget-2026-10-08.json
"""

from __future__ import annotations

import argparse
import json
import platform
import statistics
import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from evoagent.db.base import Base  # noqa: E402
from evoagent.db.models import ArtifactRecord  # noqa: E402
from evoagent.db.session import Database  # noqa: E402
from evoagent.privacy.redaction import POLICY_VERSION, detect_sensitive, redact_text  # noqa: E402
from evoagent.tasks.service import TaskService  # noqa: E402
from evoagent.tools.output_store import ToolOutputStore  # noqa: E402
from evoagent.trace.artifacts import ArtifactService, LocalArtifactStore  # noqa: E402

#: 归档下限来自 config.max_tool_result_chars 的默认值；这里显式写死并标注来源。
ARCHIVE_THRESHOLD_CHARS = 20_000
SIZES = (24_000, 65_536, 262_144, 1_048_576)
REPEATS = 3


def _ascii_log(chars: int) -> str:
    line = "2026-10-08T12:00:00Z INFO worker=1 step=collect rows=128 elapsed_ms=42\n"
    return (line * (chars // len(line) + 1))[:chars]


def _chinese_prose(chars: int) -> str:
    line = "本批次汇总需要核对编号字段的类型与唯一性，并保留前导零。\n"
    return (line * (chars // len(line) + 1))[:chars]


def _code(chars: int) -> str:
    line = "    value = resolve(settings.api_key, default=None)  # keep provenance\n"
    return (line * (chars // len(line) + 1))[:chars]


def _secret_dense(chars: int) -> str:
    line = "row {index} password: fake-value-{index}\n"
    parts: list[str] = []
    index = 0
    while sum(len(part) for part in parts) < chars:
        parts.append(line.format(index=index))
        index += 1
    return "".join(parts)[:chars]


GENERATORS: dict[str, Callable[[int], str]] = {
    "ascii_log": _ascii_log,
    "chinese_prose": _chinese_prose,
    "code": _code,
    "secret_dense": _secret_dense,
}


def _timed(operation: Callable[[str], Any], text: str) -> float:
    """返回最快一次的毫秒耗时，避免首轮导入/缓存噪声。"""

    best = float("inf")
    for _ in range(REPEATS):
        started = time.perf_counter()
        operation(text)
        best = min(best, (time.perf_counter() - started) * 1000)
    return best


def _git(*arguments: str) -> str | None:
    import subprocess

    try:
        completed = subprocess.run(
            ("git", *arguments), cwd=ROOT, capture_output=True, text=True, timeout=10, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return completed.stdout.strip() if completed.returncode == 0 else None


async def measure(workspace: Path) -> dict[str, Any]:
    database = Database(f"sqlite+aiosqlite:///{workspace / 'scan-budget.db'}")
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    service = TaskService(database.session_factory)
    session = await service.create_session("扫描预算测量")
    aggregate = await service.create_task(
        session_id=session.id, goal="测量归档尺寸与复查吞吐", provider="mock", model="mock-model"
    )
    store = LocalArtifactStore(workspace / "artifacts")
    artifacts = ArtifactService(store, database.session_factory)
    output = ToolOutputStore(aggregate.run.id, artifacts, database.session_factory)

    samples: list[dict[str, Any]] = []
    try:
        for kind, generate in GENERATORS.items():
            for target in SIZES:
                content = generate(target)
                # 真实写入路径：短于阈值直接返回（不落盘），超过则归档
                await output.preserve(content, ARCHIVE_THRESHOLD_CHARS)
                record = await _latest_artifact(database, aggregate.run.id)
                if record is None:
                    raise AssertionError(f"{kind}/{target} 没有产生归档，阈值假设不成立")
                raw = (await artifacts.read(record.uri)).decode("utf-8")
                detect_ms = _timed(detect_sensitive, raw)
                redact_ms = _timed(redact_text, raw)
                samples.append(
                    {
                        "kind": kind,
                        "target_chars": target,
                        "chars": len(raw),
                        "bytes": len(raw.encode("utf-8")),
                        "artifact_bytes": record.size_bytes,
                        "detect_ms": round(detect_ms, 3),
                        "redact_ms": round(redact_ms, 3),
                        "detect_ms_per_mb": round(detect_ms / max(len(raw) / 1_048_576, 1e-9), 2),
                        "redact_ms_per_mb": round(redact_ms / max(len(raw) / 1_048_576, 1e-9), 2),
                    }
                )
    finally:
        await database.dispose()

    # 吞吐统计只用大样本：小样本的"每 MB"是把几毫秒外推到 1 MB，冷启动噪声会被放大
    stable = [sample for sample in samples if sample["chars"] >= 262_144]
    per_mb = [sample["redact_ms_per_mb"] for sample in stable]
    smallest = min(sample["artifact_bytes"] for sample in samples)
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "environment": {
            "revision": _git("rev-parse", "HEAD"),
            "branch": _git("rev-parse", "--abbrev-ref", "HEAD"),
            "python": platform.python_version(),
            "policy_version": POLICY_VERSION,
        },
        "scope": "合成正文 + 真实写入/存储/读取路径；不是验收库的产物分布",
        "archive_threshold_chars": ARCHIVE_THRESHOLD_CHARS,
        "samples": samples,
        "derived": {
            "median_redact_ms_per_mb": round(statistics.median(per_mb), 2),
            "worst_redact_ms_per_mb": round(max(per_mb), 2),
            "stable_sample_min_chars": 262_144,
            "stable_sample_count": len(stable),
            "smallest_archived_bytes": smallest,
            "note": (
                "归档正文的下限由归档阈值决定：小于阈值的输出不落盘。"
                "因此任何扫描预算都必须显著大于该下限，否则等于拒绝注入全部归档。"
            ),
            "budget_examples": {
                f"{budget}_ms_covers_mb": round(budget / statistics.median(per_mb), 2)
                for budget in (100, 250, 500, 1000)
            },
        },
        "limits": [
            "正文为合成内容，不能代表真实任务的产物分布",
            "测的是**现有**敏感原语（privacy.redaction）的吞吐；S0b 扩大规则后须重测",
            "未测量内存占用与并发下的吞吐退化",
            "§2.5 要求的验收库覆盖率报告仍缺，本报告不能替代它",
        ],
    }


async def _latest_artifact(database: Database, run_id: Any) -> ArtifactRecord | None:
    from sqlalchemy import select

    async with database.session_factory() as session:
        return await session.scalar(
            select(ArtifactRecord)
            .where(ArtifactRecord.run_id == run_id)
            .order_by(ArtifactRecord.created_at.desc())
            .limit(1)
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", dest="json_path", default=None)
    parser.add_argument("--workspace", default="output/scan-budget")
    arguments = parser.parse_args()

    import asyncio

    workspace = ROOT / arguments.workspace
    workspace.mkdir(parents=True, exist_ok=True)
    report = asyncio.run(measure(workspace))

    print(f"{'kind':<14}{'chars':>10}{'bytes':>10}{'artifact':>10}{'redact_ms':>11}{'ms/MB':>9}")
    for sample in report["samples"]:
        print(
            f"{sample['kind']:<14}{sample['chars']:>10}{sample['bytes']:>10}"
            f"{sample['artifact_bytes']:>10}{sample['redact_ms']:>11}{sample['redact_ms_per_mb']:>9}"
        )
    derived = report["derived"]
    median = derived["median_redact_ms_per_mb"]
    worst = derived["worst_redact_ms_per_mb"]
    print(f"\n中位吞吐：{median} ms/MB（最差 {worst} ms/MB）")
    print(f"归档正文下限：{derived['smallest_archived_bytes']} 字节")
    print(f"注意：{derived['note']}")

    if arguments.json_path:
        target = ROOT / arguments.json_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"\n报告已写入：{arguments.json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
