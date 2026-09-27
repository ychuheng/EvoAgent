"""生成 M6 预注册记录（计划 §11）：样本量、`N × K` 库存与功效曲线。

计划要求"先定最小有意义效应 → 从 dev 试跑估计失配率 → 用配对二分类检验的精确计算求
双侧 0.05、至少 80% 检出力所需配对数 → 每个要单独声称有效的任务家族不少于 30 对"。

**失配率必须来自 dev 试跑实测**，本脚本不会编造它。做法是把"失配率 → 所需配对数 → 可检出效应"
的完整计算结果先写进预注册，正式运行前只需把实测失配率填进去（或在预注册里挑定对应行），
这样"看 holdout 之前就冻结样本量"是可核对的。

用法：
    python scripts/m6_preregistration.py [--output docs/evaluations/M6预注册-2026-09-27.md]
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from pathlib import Path

from evoagent.skills.power import (
    DEFAULT_ALPHA,
    DEFAULT_MIN_EFFECT,
    DEFAULT_POWER,
    MIN_PAIRS_REQUIRED,
    detectable_effect,
    maximum_power,
    plan_for_family,
    power_curve,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "docs/evaluations/M6预注册-2026-09-27.md"
# 候选失配率：dev 试跑实测后应落在其中之一附近；这里覆盖"低到高"的常见范围。
MISMATCH_CANDIDATES = (0.10, 0.15, 0.20, 0.25, 0.30, 0.40)
# 计划下限与几档备选样本量，用于说明"样本量受限时只能检出多少"。
SAMPLE_SIZES = (30, 50, 100, 150, 200, 300)
CANDIDATE_ROUNDS = 1


def render() -> str:
    lines: list[str] = [
        "# M6 预注册记录（计划 §11）",
        "",
        "> 本文件由 `scripts/m6_preregistration.py` 生成，数字来自 `evoagent.skills.power` 的",
        "> **精确**配对二分类检验计算（双侧 0.05、目标检出力 80%）。",
        ">",
        "> **失配率一栏仍是候选值**：计划要求「从 dev 试跑估计配对两臂的失配率」，而本仓库",
        "> 目前没有真实模型试跑结果，因此不能填一个实测值。正式运行前必须先把实测失配率填进来",
        "> （或在预注册里挑定对应行）并冻结，**不得**在看到 holdout 结果之后再选。",
        "",
        "## 1. 冻结的参数",
        "",
        "| 参数 | 值 | 来源 |",
        "| --- | --- | --- |",
        f"| 最小有意义效应 | **{DEFAULT_MIN_EFFECT:.0%}**（绝对提升） | "
        "计划 §11：固定 10 个百分点 |",
        f"| 双侧显著性水平 | {DEFAULT_ALPHA} | 计划 §11 |",
        f"| 目标检出力 | {DEFAULT_POWER:.0%} | 计划 §11 |",
        f"| 每个家族最少配对数 | {MIN_PAIRS_REQUIRED} | 计划 §11 下限；不保证检出 10 个百分点 |",
        f"| 首轮候选轮数 K | {CANDIDATE_ROUNDS} | 计划 §11「首轮默认只承诺 K=1」 |",
        "| 检验方法 | 精确配对二分类检验（McNemar），只用不一致对 | 计划 §11「精确计算或模拟」 |",
        "| 最小有意义效应是否可调整 | **不可**为适应预算从 10 改成 20 | 计划 §11 明文禁止 |",
        "",
        "## 2. 失配率 → 所需配对数（候选值）",
        "",
        "| 失配率（候选） | 计算所需配对数 | 实际检出力 | 期望不一致对 | 该 N 下可检出效应 |"
        " 需备 holdout（`N × K`） |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for mismatch in MISMATCH_CANDIDATES:
        plan = plan_for_family(mismatch, candidates=CANDIDATE_ROUNDS)
        detectable = plan["detectable_effect_at_planned_pairs"]
        if isinstance(detectable, float):
            effect_cell = f"{detectable:.1%}"
        else:  # pragma: no cover - 当前候选失配率下不会发生
            effect_cell = f"不可达（最高 {plan['max_power_at_planned_pairs']:.1%}）"
        lines.append(
            f"| {mismatch:.0%} | {plan['planned_pairs']} | {plan['achieved_power']:.3f} | "
            f"{plan['expected_discordant']} | {effect_cell} | {plan['required_holdout_pairs']} |"
        )
    lines += [
        "",
        "**怎么用这张表**：dev 试跑测出两臂结果不一致的比例后，在表中找到对应的失配率行，",
        "取该行的「计算所需配对数」作为 `N`（若实测值落在两行之间，取**较大**的一侧，",
        "并按实际值重跑 `scripts/m6_preregistration.py` 重新冻结）。",
        "",
        "## 3. 样本量受限时能检出多少",
        "",
        "计划要求探索性报告写明「本样本量在 80% 检出力下只能检出至少 X 个百分点的绝对提升」。",
        "下表即该数值：**在给定失配率下，这些配对数只能检出这么大的效应**。",
        "",
        "| 配对数 ↓ / 失配率 → | "
        + " | ".join(f"{item:.0%}" for item in MISMATCH_CANDIDATES)
        + " |",
        "| --- | " + " | ".join("---" for _ in MISMATCH_CANDIDATES) + " |",
    ]
    for pairs in SAMPLE_SIZES:
        cells = []
        for mismatch in MISMATCH_CANDIDATES:
            effect = detectable_effect(pairs, mismatch)
            if effect is None:
                # 连"全部不一致对同向"都达不到 80% 检出力：如实标为不可达，并给出最高检出力。
                top = maximum_power(pairs, mismatch)
                cells.append(f"不可达（最高 {top:.1%}）")
            else:
                cells.append(f"{effect:.1%}")
        lines.append(f"| {pairs} | " + " | ".join(cells) + " |")
    unreachable = [
        (pairs, mismatch)
        for pairs in SAMPLE_SIZES
        for mismatch in MISMATCH_CANDIDATES
        if detectable_effect(pairs, mismatch) is None
    ]
    lines += [
        "",
        "「不可达」的含义：在 80% 检出力下**没有任何**效应是可检出的（连「全部不一致对都同向」",
        "也达不到 80%），括号里是该配对数与失配率下的最高检出力。此时只能加大样本量，",
        "或按计划的「样本量受限」条款降级为探索性报告，**不得**把最高检出力当成检出力来写。",
    ]
    if unreachable:
        rendered = "、".join(f"{pairs} 对 × {mismatch:.0%}" for pairs, mismatch in unreachable)
        lines.append(f"上表中的不可达格：{rendered}。")
    lines += [
        f"注意 {MIN_PAIRS_REQUIRED} 对（计划下限）这一行的含义：只有当失配率足够高时它才够用；"
        "失配率低时（如 10%）连 10 个百分点都检不出，",
        "所以「30 对」只是防止极小样本，绝不能用来声称 10 个百分点的收益。",
        "",
        "## 4. 功效曲线（供报告直接引用）",
        "",
        "| 失配率 | 配对数 | 10pp | 15pp | 20pp | 30pp |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for mismatch in MISMATCH_CANDIDATES:
        rows = power_curve(mismatch, pair_counts=(30, 100, 200, 300))
        by_pairs: dict[int, dict[float, float]] = {}
        for row in rows:
            by_pairs.setdefault(int(row["pairs"]), {})[row["effect"]] = row["power"]
        for pairs in (30, 100, 200, 300):
            cells = by_pairs.get(pairs, {})
            rendered = [
                f"{cells[effect]:.3f}" if effect in cells else "—"
                for effect in (0.10, 0.15, 0.20, 0.30)
            ]
            lines.append(f"| {mismatch:.0%} | {pairs} | " + " | ".join(rendered) + " |")
    lines += [
        "",
        "## 5. 预注册时必须一并冻结的内容",
        "",
        "1. **主要任务家族**（只能有一个作为主要声称对象）与它的 holdout 样本清单；",
        "2. **实测失配率**及其来源（哪次 dev 试跑、多少对、原始计数）；",
        "3. 由此得到的 `N` 与 `N × K` 库存数量，以及这些样本**已经入库封存**的证据",
        "   （`evals/datasets/manifest.json` + `scripts/check_sample_ledger.py` 通过）；",
        "4. 模型与工具版本、预算额度（见[付费与漂移闸门](付费与漂移闸门-M0c.md)）；",
        "5. 人评规则版本（[rubric-1](人评表模板.md)）与第三方复核预留在哪些样本上。",
        "",
        "## 6. 本文件明确不做的事",
        "",
        "- **不编造实测失配率**：第 2 节列的是候选值，不是测量结果。",
        "- **不声称任何 Skill 收益**：这里只有样本量设计，没有实验结果。",
        "- **不为适应预算放宽效应**：最小有意义效应固定 10 个百分点；预算不足时按计划改为",
        "  「样本量受限的探索性试验」，并在报告中写明可检出效应（第 3 节）。",
        "",
        f"> 生成时间：{datetime.now(UTC).isoformat()}",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(render() + "\n", encoding="utf-8")
    print(f"已写出 {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
