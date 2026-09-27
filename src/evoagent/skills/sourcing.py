"""M6 S-01：Skill 提炼来源的资格审查（来源集合复查）。

计划 §11 的原话：

> 提炼素材必须来自已核对的 dev/日常真实任务……**M6/M7 holdout 的任务、运行轨迹和评审结果
> 一律不得作为 Skill 提炼来源**。

已有的 `provenance.TraceEligibilityChecker` 管的是"EvalRun 是否 TRAIN、是否通过校验、
是否可复现"，它不认识 M0b 台账。这里补的是**集合维度**的复查：根据 M0b 台账
（`evals/datasets/manifest.json` + `run_ledger.jsonl`）判断一条运行属于 dev、m6-holdout
还是 m7-holdout，并把 holdout 一律拒绝——**包括只是看过、还没评分的轨迹**。

判定顺序（保守优先）：

1. 出现在正式使用台账 `run_ledger.jsonl` 里的任何 run——不论它是否已评分——一律不可作来源；
2. 由 M6/M7 数据集（`m6-*` / `m7-*`）产生的运行一律不可作来源；
3. 其余视为 dev/日常运行，可以作来源，但必须由人工确认，并满足计划 S-01 的记录要求。
"""

from __future__ import annotations

import fnmatch
import json
from dataclasses import dataclass
from pathlib import Path

HOLDOUT_PREREGISTRATION_PREFIXES = ("m6-", "m7-")
LEGACY_DATASET_MARKERS = ("legacy",)


@dataclass(frozen=True, slots=True)
class SourceVerdict:
    allowed: bool
    set_name: str
    reason: str


def load_ledger(datasets_root: Path) -> tuple[dict, set[str]]:
    """读取 M0b 台账；返回 (manifest, 正式使用过的 run_id 集合)。"""

    manifest_path = datasets_root / "manifest.json"
    manifest = (
        json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest_path.is_file()
        else {"samples": [], "fixture_reviews": {}}
    )
    used_runs: set[str] = set()
    ledger_path = datasets_root / "run_ledger.jsonl"
    if ledger_path.is_file():
        for line in ledger_path.read_text(encoding="utf-8-sig").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            run_id = row.get("run_id")
            if isinstance(run_id, str):
                used_runs.add(run_id)
    return manifest, used_runs


def classify_sample_set(manifest: dict, *, dataset: str | None, split: str | None) -> str:
    """按台账与数据集名判断样本集合。"""

    for sample in manifest.get("samples", []):
        if dataset is not None and sample.get("dataset") == dataset:
            return str(sample.get("set") or "unknown")
    if dataset is None:
        return "unknown"
    lowered = Path(dataset).name.lower()
    if lowered.startswith(HOLDOUT_PREREGISTRATION_PREFIXES):
        return "holdout-by-name"
    if any(marker in lowered for marker in LEGACY_DATASET_MARKERS):
        return "legacy"
    if split is not None and split != "train":
        return "non-train-split"
    return "unknown"


def classify_trace_source(
    *,
    manifest: dict,
    used_runs: set[str],
    run_id: str | None = None,
    dataset: str | None = None,
    split: str | None = None,
    sample_set: str | None = None,
) -> SourceVerdict:
    """判断一条运行/样本能否作为 Skill 提炼来源。"""

    # 1) 台账里出现过的 run：包括"未评分但已看过"的，一律拒绝。
    if run_id is not None and run_id in used_runs:
        return SourceVerdict(
            allowed=False,
            set_name="holdout-used",
            reason=(
                f"run {run_id} 已出现在正式使用台账里（可能已评分，也可能只是看过轨迹）；"
                "计划禁止把 M6/M7 holdout 的运行作为 Skill 提炼来源"
            ),
        )

    resolved = sample_set or classify_sample_set(manifest, dataset=dataset, split=split)
    if resolved in {"m6-holdout", "m7-holdout", "holdout-by-name"}:
        return SourceVerdict(
            allowed=False,
            set_name=resolved,
            reason=f"样本属于 {resolved}；holdout 的任务与轨迹不得用于提炼 Skill",
        )
    if resolved == "legacy":
        return SourceVerdict(
            allowed=False,
            set_name=resolved,
            reason="历史已曝光样本：既不能充当 holdout，也不适合作为提炼来源",
        )
    if resolved == "non-train-split":
        return SourceVerdict(
            allowed=False,
            set_name=resolved,
            reason="非 TRAIN 切分不得作为提炼来源（与 provenance 检查一致）",
        )
    if resolved == "dev":
        return SourceVerdict(
            allowed=True,
            set_name="dev",
            reason="dev 样本，可用于提炼；仍需人工确认成功条件与适用边界",
        )
    return SourceVerdict(
        allowed=False,
        set_name=resolved,
        reason="无法从台账判定该运行属于哪个集合；先登记样本再提炼，避免误用 holdout",
    )


def review_human_confirmation(record: dict) -> SourceVerdict:
    """S-01 要求每条候选可追溯、并标注成功证据与失败案例。"""

    required = ("task_family", "tool_versions", "success_evidence")
    missing = [field for field in required if not record.get(field)]
    if missing:
        return SourceVerdict(
            allowed=False,
            set_name="incomplete",
            reason="缺人工确认信息：" + "、".join(missing),
        )
    if not record.get("human_confirmed"):
        return SourceVerdict(
            allowed=False,
            set_name="unconfirmed",
            reason="未经人工确认的有效案例不得进入提炼",
        )
    failures = record.get("failure_cases")
    if not isinstance(failures, list) or not failures:
        return SourceVerdict(
            allowed=False,
            set_name="no-failure-cases",
            reason="S-01 要求标注常见失败；至少要记录一条失败案例",
        )
    return SourceVerdict(allowed=True, set_name="confirmed", reason="人工确认信息齐全")


def find_holdout_dataset_files(datasets_root: Path) -> list[str]:
    """列出属于 holdout 的数据集文件名，供检查脚本给出可读提示。"""

    names: list[str] = []
    for path in sorted(datasets_root.glob("*.json")):
        if fnmatch.fnmatch(path.name, "m6-*") or fnmatch.fnmatch(path.name, "m7-*"):
            names.append(path.name)
    return names
