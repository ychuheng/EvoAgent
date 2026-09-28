"""M6 第一步：用 dev 试跑的配对结果算出**失配率**，再据此定所需配对数 `N`。

计划 §11「样本量与预算先后」的顺序：先定最小有意义效应（10 个百分点）→ **从 dev 试跑估计
失配率** → 精确计算双侧 0.05、至少 80% 检出力所需配对数 → 再决定是否有条件做正式实验。

本脚本只负责**换算与判定**：输入是 dev 试跑的两臂配对结果（每条样本 control/treatment 是否成功），
输出是失配率、`N`、`N × K` 与"是否够条件开正式实验"。**它不发起任何模型调用**，
所以可以在没有费用授权时先跑通、先审阅。

输入格式：

```json
{
  "schema_version": 1,
  "source": "dev 试跑说明（哪个部署、哪次运行）",
  "pairs": [
    {"dev_case": "m3-fail-then-fix", "control_success": true, "treatment_success": false}
  ]
}
```

用法：
    python scripts/m6_dev_mismatch.py --outcomes output/m6-dev-pilot.json
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from evoagent.skills.power import MIN_PAIRS_REQUIRED, required_pairs

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "output/m6-dev-mismatch.json"


def mismatch_rate(pairs: list[dict]) -> dict:
    """配对结果的失配率：两臂不一致的比例（配对设计里只有不一致对提供信息）。"""

    if not pairs:
        raise ValueError("没有任何配对结果")
    control_only = treatment_only = both_success = both_failure = 0
    for item in pairs:
        control = item.get("control_success")
        treatment = item.get("treatment_success")
        if not isinstance(control, bool) or not isinstance(treatment, bool):
            raise ValueError("每条配对结果都要有布尔 control_success / treatment_success")
        if control and treatment:
            both_success += 1
        elif control and not treatment:
            control_only += 1
        elif treatment and not control:
            treatment_only += 1
        else:
            both_failure += 1
    total = len(pairs)
    discordant = control_only + treatment_only
    return {
        "pairs": total,
        "both_success": both_success,
        "both_failure": both_failure,
        "control_only": control_only,
        "treatment_only": treatment_only,
        "discordant": discordant,
        "mismatch_rate": discordant / total,
    }


def plan_from_measured(mismatch: float, *, candidates: int = 1, effect: float = 0.10) -> dict:
    """把实测失配率代进精确计算，给出 `N` 与 `N × K`。"""

    if candidates < 1:
        raise ValueError("候选轮数 K 至少为 1")
    if mismatch < 0 or mismatch > 1:
        raise ValueError("失配率必须在 [0, 1]")
    if mismatch == 0:
        return {
            "mismatch_rate": mismatch,
            "feasible": False,
            "reason": (
                "两臂结果完全没有差异：要么这些 dev 样本区分不出 Skill 效果，要么两臂其实"
                "跑的是同一配置。**不能**据此缩小样本量，先换能区分两臂的 dev 样本。"
            ),
            "floor_pairs": MIN_PAIRS_REQUIRED,
        }
    if mismatch < effect:
        return {
            "mismatch_rate": mismatch,
            "feasible": False,
            "reason": (
                f"失配率 {mismatch:.1%} 小于最小有意义效应 {effect:.0%}：在该失配率下"
                "10 个百分点**不可能**被检出，需要重选任务家族或效应"
            ),
            "floor_pairs": MIN_PAIRS_REQUIRED,
        }
    result = required_pairs(mismatch, effect)
    return {
        "mismatch_rate": mismatch,
        "feasible": True,
        "planned_pairs": result.pairs,
        "required_holdout_pairs": result.pairs * candidates,
        "candidates_k": candidates,
        "achieved_power": round(result.power, 4),
        "expected_discordant": result.expected_discordant,
        "floor_pairs": MIN_PAIRS_REQUIRED,
        "note": "把 `N × K` 写进预注册并在看 holdout 结果之前冻结；不得事后改小",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--outcomes", type=Path, required=True, help="dev 试跑的配对结果 JSON")
    parser.add_argument("--candidates", type=int, default=1, help="计划候选轮数 K")
    parser.add_argument("--effect", type=float, default=0.10, help="最小有意义效应（默认 0.10）")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    document = json.loads(args.outcomes.read_text(encoding="utf-8-sig"))
    if document.get("schema_version") != 1:
        raise SystemExit("配对结果文件的 schema_version 必须是 1")
    pairs = document.get("pairs")
    if not isinstance(pairs, list):
        raise SystemExit("配对结果文件缺少 pairs 数组")
    if document.get("set") not in (None, "dev"):
        raise SystemExit("只允许用 dev 样本估计失配率")

    measured = mismatch_rate(pairs)
    plan = plan_from_measured(
        measured["mismatch_rate"], candidates=args.candidates, effect=args.effect
    )
    report = {
        "schema_version": 1,
        "computed_at": datetime.now(UTC).isoformat(),
        "source": document.get("source"),
        "measured": measured,
        "plan": plan,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({"measured": measured, "plan": plan}, ensure_ascii=False, indent=2))
    print(f"输出 {args.output}")
    return 0 if plan["feasible"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
