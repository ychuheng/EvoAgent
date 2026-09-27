"""自检：把计划 §5.5 的每条拒绝规则各喂一次，确认检查脚本真的拒绝。

这是脚本的一次性验证工具，不进入 CI（它刻意构造非法台账，并临时改写
`evals/datasets/manifest.json`；结束时恢复原内容）。用法：
    python scripts/selftest_sample_ledger.py
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import check_sample_ledger as ledger  # noqa: E402

M6_SAMPLES = ["m6-config-retry-limit", "m6-config-env-override", "m6-currency-default"]
M7_SAMPLES = ["m7-map-entry", "m7-edit-chunk-reading"]
HEAD = {
    "candidate_revision": "cand-1",
    "model": "deepseek-chat",
    "tools_revision": "tools-1",
    "dataset_sha256": "0" * 64,
}


def row(**overrides) -> dict:
    base = {
        "run_id": "run-0001",
        "experiment_id": "exp-0001",
        "candidate_id": "skill-distill-v1",
        "milestone": "M6",
        **HEAD,
        "arms": {"control": M6_SAMPLES[:2], "treatment": M6_SAMPLES[2:]},
    }
    base.update(overrides)
    return base


def audit_with(rows: list[dict], *, retired: tuple[str, ...] = ()) -> ledger.Audit:
    """在临时把若干样本标为「已看过」的台账上跑一次正式使用检查。"""

    original = ledger.MANIFEST.read_text(encoding="utf-8")
    try:
        if retired:
            document = json.loads(original)
            for sample in document["samples"]:
                if sample["sample_id"] in retired:
                    sample["retired_after_viewing"] = True
            ledger.MANIFEST.write_text(
                json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
        audit = ledger.Audit()
        by_id = ledger.check_manifest(
            json.loads(ledger.MANIFEST.read_text(encoding="utf-8")), audit
        )
        for number, item in enumerate(rows, start=1):
            item["_line"] = number
        ledger.check_run_ledger(rows, by_id, audit)
        return audit
    finally:
        ledger.MANIFEST.write_text(original, encoding="utf-8")


def audit_review(update) -> ledger.Audit:
    """改一条 fixture 人审记录后跑结构检查，用于验证抽查完成度校验真的生效。"""

    original = ledger.MANIFEST.read_text(encoding="utf-8")
    try:
        document = json.loads(original)
        update(document["fixture_reviews"]["ledger-holdout"])
        ledger.MANIFEST.write_text(
            json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        audit = ledger.Audit()
        ledger.check_manifest(json.loads(ledger.MANIFEST.read_text(encoding="utf-8")), audit)
        return audit
    finally:
        ledger.MANIFEST.write_text(original, encoding="utf-8")


CASES: list[tuple[str, list[dict], tuple[str, ...], str]] = [
    ("合法的一行两臂", [row()], (), ""),
    (
        "run_id 重复（台账被改写）",
        [row(), row(experiment_id="exp-0002", candidate_id="cand-2")],
        (),
        "台账只能追加",
    ),
    (
        "experiment_id 重复（一次正式使用写成两行）",
        [row(), row(run_id="run-0002", candidate_id="cand-2")],
        (),
        "一次正式使用只允许一行",
    ),
    (
        "同一候选第二次正式运行",
        [
            row(),
            row(run_id="run-0002", experiment_id="exp-0002", milestone="M7"),
        ],
        (),
        "每个候选版本只能对封存 holdout 作一次正式运行",
    ),
    (
        "holdout 跨实验复用",
        [
            row(),
            row(
                run_id="run-0002",
                experiment_id="exp-0002",
                candidate_id="skill-distill-v2",
                arms={"control": M6_SAMPLES[:1], "treatment": M6_SAMPLES[1:2]},
            ),
        ],
        (),
        "不得跨实验复用",
    ),
    (
        "M6 使用 M7 集合的样本",
        [row(arms={"control": M7_SAMPLES[:1], "treatment": M7_SAMPLES[1:2]})],
        (),
        "必须使用 m6-holdout",
    ),
    (
        "引用不存在的样本",
        [row(arms={"control": ["m6-does-not-exist"], "treatment": M6_SAMPLES[:1]})],
        (),
        "台账中不存在",
    ),
    (
        "legacy 样本充当 holdout",
        [row(arms={"control": ["legacy:agent-core-user-v1"], "treatment": M6_SAMPLES[:1]})],
        (),
        "历史已曝光样本",
    ),
    ("milestone 非法", [row(milestone="M8")], (), "milestone 必须是 M6 或 M7"),
    (
        "缺 model 等冻结信息",
        [{k: v for k, v in row().items() if k != "model"}],
        (),
        "缺少 model",
    ),
    (
        "已看过的样本再次充当 holdout",
        [row(arms={"control": M6_SAMPLES[:1], "treatment": M6_SAMPLES[1:2]})],
        (M6_SAMPLES[0],),
        "retired_after_viewing",
    ),
    (
        "同一样本同时出现在两臂（配对实验必须互斥）",
        [row(arms={"control": M6_SAMPLES[:2], "treatment": M6_SAMPLES[1:3]})],
        (),
        "两臂必须使用互不相同的样本",
    ),
]


REVIEW_CASES: list[tuple[str, Callable[[dict], object], str]] = [
    (
        "抽查记录缺少 reviewer_role",
        lambda review: review.pop("reviewer_role", None),
        "reviewer_role",
    ),
    (
        "抽查记录缺少抽查维度",
        lambda review: review.pop("reviewed_scope", None),
        "reviewed_scope",
    ),
    (
        "抽查完成数与维度数不一致",
        lambda review: review.update({"review_scope_total": 99}),
        "review_scope_total",
    ),
    (
        "自审不得伪装成第三方",
        lambda review: review.update({"reviewer_role": "somebody_else"}),
        "reviewer_role",
    ),
]


def main() -> int:
    failed = 0
    for title, rows, retired, expected in CASES:
        audit = audit_with([dict(item) for item in rows], retired=retired)
        ok = any(expected in message for message in audit.errors) if expected else not audit.errors
        print(f"[{'通过' if ok else '失败'}] {title}")
        if not ok:
            failed += 1
            print("  期望包含：" + (expected or "（无错误）"))
            print("  实际错误：" + ("；".join(audit.errors) or "（无错误）"))
    for title, update, expected in REVIEW_CASES:
        audit = audit_review(update)
        ok = any(expected in message for message in audit.errors)
        print(f"[{'通过' if ok else '失败'}] {title}")
        if not ok:
            failed += 1
            print("  期望包含：" + expected)
            print("  实际错误：" + ("；".join(audit.errors) or "（无错误）"))
    total = len(CASES) + len(REVIEW_CASES)
    print(f"自检结束：{total - failed}/{total} 通过")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
