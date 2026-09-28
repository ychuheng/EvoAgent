"""M6 S-01 来源资格审查的测试。

计划 §11：提炼素材必须来自已核对的 dev/日常真实任务；**M6/M7 holdout 的任务、运行轨迹和
评审结果一律不得作为 Skill 提炼来源**。这组测试把"集合判定"与"已看过即拒绝"钉住。
"""

from __future__ import annotations

import json
from pathlib import Path

from evoagent.skills.sourcing import (
    classify_sample_set,
    classify_trace_source,
    find_holdout_dataset_files,
    load_dev_run_ids,
    load_ledger,
    review_human_confirmation,
)


def write_ledger(root: Path, *, samples: list[dict], runs: list[dict] | None = None) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "manifest.json").write_text(
        json.dumps({"schema_version": 1, "samples": samples}, ensure_ascii=False),
        encoding="utf-8",
    )
    if runs is not None:
        (root / "run_ledger.jsonl").write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in runs),
            encoding="utf-8",
        )


def test_dev_sample_is_allowed(tmp_path: Path) -> None:
    write_ledger(
        tmp_path,
        samples=[
            {
                "sample_id": "map-01",
                "dataset": "m0a-project-dev-v1.json",
                "set": "dev",
                "task_family": "project_reading_dev",
            }
        ],
    )
    manifest, used = load_ledger(tmp_path)

    verdict = classify_trace_source(
        manifest=manifest, used_runs=used, dataset="m0a-project-dev-v1.json"
    )

    assert verdict.allowed is True
    assert verdict.set_name == "dev"


def test_holdout_sample_is_refused(tmp_path: Path) -> None:
    write_ledger(
        tmp_path,
        samples=[
            {"sample_id": "m6-x", "dataset": "m6-skill-holdout-v1.json", "set": "m6-holdout"},
            {"sample_id": "m7-y", "dataset": "m7-release-holdout-v1.json", "set": "m7-holdout"},
        ],
    )
    manifest, used = load_ledger(tmp_path)

    for dataset in ("m6-skill-holdout-v1.json", "m7-release-holdout-v1.json"):
        verdict = classify_trace_source(manifest=manifest, used_runs=used, dataset=dataset)
        assert verdict.allowed is False
        assert "holdout" in verdict.reason


def test_any_run_in_the_formal_ledger_is_refused_even_without_scoring(tmp_path: Path) -> None:
    """计划明确包含"未评分但已看过的轨迹"——台账里出现过就不行。"""

    write_ledger(
        tmp_path,
        samples=[{"sample_id": "map-01", "dataset": "m0a-project-dev-v1.json", "set": "dev"}],
        runs=[
            {
                "run_id": "run-m6-0001",
                "experiment_id": "exp-0001",
                "candidate_id": "skill-a",
                "milestone": "M6",
                "arms": {"control": ["m6-x"], "treatment": ["m6-y"]},
            }
        ],
    )
    manifest, used = load_ledger(tmp_path)
    assert used == {"run-m6-0001"}

    verdict = classify_trace_source(
        manifest=manifest, used_runs=used, run_id="run-m6-0001", dataset="m6-skill-holdout-v1.json"
    )

    assert verdict.allowed is False
    assert verdict.set_name == "holdout-used"
    assert "看过" in verdict.reason


def test_legacy_and_unknown_are_refused(tmp_path: Path) -> None:
    write_ledger(
        tmp_path,
        samples=[
            {"sample_id": "legacy:x", "dataset": "agent-core-user-v1.json", "set": "legacy"},
            {"sample_id": "y", "dataset": "personal-research-v1.json", "set": "legacy"},
        ],
    )
    manifest, used = load_ledger(tmp_path)

    legacy = classify_trace_source(
        manifest=manifest, used_runs=used, dataset="agent-core-user-v1.json"
    )
    unknown = classify_trace_source(manifest=manifest, used_runs=used, dataset="something-new.json")

    assert legacy.allowed is False and legacy.set_name == "legacy"
    assert unknown.allowed is False
    assert "dev_runs.jsonl" in unknown.reason


def test_registered_dev_run_is_allowed_but_unregistered_is_not(tmp_path: Path) -> None:
    """S-01 的允许路径：只有登记在 dev_runs.jsonl 的轨迹才能提炼；没登记的一律拒绝。"""

    write_ledger(tmp_path, samples=[])
    (tmp_path / "dev_runs.jsonl").write_text(
        '{"run_id": "dev-1", "task_id": "task-1", "task_family": "failure_fix", "set": "dev"}\n',
        encoding="utf-8",
    )
    manifest, used = load_ledger(tmp_path)
    dev_runs = load_dev_run_ids(tmp_path)

    registered = classify_trace_source(
        manifest=manifest,
        used_runs=used,
        dev_runs=dev_runs,
        run_id="dev-1",
        dataset="m3-failure-fix-dev-v1.json",
        split="train",
    )
    assert registered.allowed is True and registered.set_name == "dev"

    other = classify_trace_source(
        manifest=manifest,
        used_runs=used,
        dev_runs=dev_runs,
        run_id="dev-2",
        dataset="m3-failure-fix-dev-v1.json",
        split="train",
    )
    assert other.allowed is False

    # 已登记为 dev 的 run 一旦出现在正式使用台账里（说明它其实是 holdout 运行），必须拒绝。
    holdout_used = classify_trace_source(
        manifest=manifest,
        used_runs={"dev-1"},
        dev_runs={"dev-1"},
        run_id="dev-1",
        dataset="m3-failure-fix-dev-v1.json",
        split="train",
    )
    assert holdout_used.allowed is False and holdout_used.set_name == "holdout-used"


def test_holdout_dataset_name_is_refused_without_a_manifest_entry(tmp_path: Path) -> None:
    """台账没登记但文件名就是 holdout：必须按名字拒绝，不能默认放行。"""

    write_ledger(tmp_path, samples=[])
    manifest, used = load_ledger(tmp_path)

    verdict = classify_trace_source(
        manifest=manifest, used_runs=used, dataset="/tmp/m6-skill-holdout-v1.json"
    )

    assert verdict.allowed is False
    assert verdict.set_name == "holdout-by-name"


def test_non_train_split_is_refused(tmp_path: Path) -> None:
    write_ledger(tmp_path, samples=[])
    manifest, used = load_ledger(tmp_path)

    verdict = classify_trace_source(
        manifest=manifest, used_runs=used, dataset="unregistered.json", split="holdout"
    )

    assert verdict.allowed is False
    assert verdict.set_name == "non-train-split"


def test_human_confirmation_requires_evidence_and_failures() -> None:
    incomplete = review_human_confirmation({"task_family": "code_comprehension"})
    assert incomplete.allowed is False and "缺人工确认信息" in incomplete.reason

    unconfirmed = review_human_confirmation(
        {
            "task_family": "code_comprehension",
            "tool_versions": "tools-1",
            "success_evidence": "trace hash",
        }
    )
    assert unconfirmed.allowed is False and "人工确认" in unconfirmed.reason

    no_failures = review_human_confirmation(
        {
            "task_family": "code_comprehension",
            "tool_versions": "tools-1",
            "success_evidence": "trace hash",
            "human_confirmed": True,
            "failure_cases": [],
        }
    )
    assert no_failures.allowed is False and "失败" in no_failures.reason

    ok = review_human_confirmation(
        {
            "task_family": "code_comprehension",
            "tool_versions": "tools-1",
            "success_evidence": "trace hash",
            "human_confirmed": True,
            "failure_cases": ["misread two callers as one"],
        }
    )
    assert ok.allowed is True


def test_classify_sample_set_uses_manifest_first() -> None:
    manifest = {
        "samples": [{"sample_id": "a", "dataset": "m6-skill-holdout-v1.json", "set": "m6-holdout"}]
    }
    assert classify_sample_set(manifest, dataset="m6-skill-holdout-v1.json", split="holdout") == (
        "m6-holdout"
    )
    assert classify_sample_set(manifest, dataset=None, split=None) == "unknown"


def test_find_holdout_dataset_files_lists_only_holdout_json(tmp_path: Path) -> None:
    for name in (
        "m6-skill-holdout-v1.json",
        "m7-release-holdout-v1.json",
        "m0a-project-dev-v1.json",
        "manifest.json",
    ):
        (tmp_path / name).write_text("{}", encoding="utf-8")

    assert find_holdout_dataset_files(tmp_path) == [
        "m6-skill-holdout-v1.json",
        "m7-release-holdout-v1.json",
    ]


def test_real_repository_holdout_sets_are_refused() -> None:
    """用仓库里的真实台账跑一次：M6/M7 数据集必须全部被拒绝。"""

    datasets_root = Path(__file__).resolve().parents[2] / "evals" / "datasets"
    manifest, used = load_ledger(datasets_root)
    assert manifest["samples"], "仓库台账应当有样本"

    holdout_datasets = [
        sample["dataset"]
        for sample in manifest["samples"]
        if sample.get("set") in {"m6-holdout", "m7-holdout"}
    ]
    assert holdout_datasets

    for dataset in holdout_datasets:
        verdict = classify_trace_source(manifest=manifest, used_runs=used, dataset=dataset)
        assert verdict.allowed is False, dataset

    dev_verdict = classify_trace_source(
        manifest=manifest, used_runs=used, dataset="m0a-project-dev-v1.json"
    )
    assert dev_verdict.allowed is True
