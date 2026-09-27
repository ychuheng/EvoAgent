"""M6 样本量计算的测试：精确 McNemar 检验、功效、配对数与可检出效应。

计划 §11 要求"用配对二分类检验的精确计算或模拟求双侧显著性水平 0.05、至少 80% 检出力
所需的配对数"，并要求 30 对下限、`N × K` 库存与"不得把 10 改成 20 以适应预算"。
这组测试把上面每条钉在具体数值上。
"""

from __future__ import annotations

import pytest

from evoagent.skills.power import (
    DEFAULT_ALPHA,
    MIN_PAIRS_REQUIRED,
    PowerAnalysisError,
    detectable_effect,
    exact_p_value,
    maximum_power,
    plan_for_family,
    power_curve,
    power_for,
    rejection_bounds,
    required_pairs,
)


def test_exact_p_value_matches_hand_computation() -> None:
    """K=5、全部偏向一侧时 p = 2 * 0.5^5 = 0.0625。"""

    assert exact_p_value(5, 0) == pytest.approx(0.0625)
    # 对称：全部偏向另一侧同样 p=0.0625（2 * 0.5^5），不是 1。
    assert exact_p_value(5, 5) == pytest.approx(0.0625)
    assert exact_p_value(5, 1) == pytest.approx(0.375)
    # 完全一致的中间结果 p = 1。
    assert exact_p_value(8, 4) == pytest.approx(1.0)


def test_rejection_is_not_claimed_for_small_discordant_counts() -> None:
    """不一致对太少时**无法**在 0.05 水平拒绝：K<=5 时连"全部偏向一侧"都还不显著。"""

    for k in (1, 2, 3, 4, 5):
        assert rejection_bounds(k) is None
    # K=6 起，"全部偏向 treatment"达到 p = 2 * 0.5^6 = 0.03125 < 0.05：拒绝域 {0, 6}。
    assert rejection_bounds(6) == (1, 5)
    assert rejection_bounds(8) == (1, 7)
    assert rejection_bounds(0) is None


def test_rejection_region_boundaries_are_exact() -> None:
    """拒绝域外侧必须 p <= alpha，内侧必须 p > alpha，且中间"完全一致"不是拒绝。"""

    for k in (6, 8, 10, 20, 50):
        bounds = rejection_bounds(k)
        assert bounds is not None
        lower, upper = bounds
        assert exact_p_value(k, max(lower - 1, 0)) <= DEFAULT_ALPHA
        # 上侧外侧：upper+1 若超出 k 则说明该侧没有拒绝区，跳过。
        if upper + 1 <= k:
            assert exact_p_value(k, min(upper + 1, k)) <= DEFAULT_ALPHA
        assert exact_p_value(k, lower) > DEFAULT_ALPHA
        assert exact_p_value(k, upper) > DEFAULT_ALPHA
        assert exact_p_value(k, k // 2) == pytest.approx(1.0)


def test_power_is_monotonic_and_bounded() -> None:
    powers = [power_for(n, 0.30, 0.10) for n in (30, 60, 100, 200, 300)]
    assert powers == sorted(powers)
    assert all(0.0 <= item <= 1.0 for item in powers)
    assert powers[0] < 0.5  # 30 对在 m=0.30 下远达不到 80%


def test_zero_effect_power_equals_alpha() -> None:
    assert power_for(100, 0.30, 0.0) == pytest.approx(DEFAULT_ALPHA)


def test_effect_larger_than_mismatch_is_rejected() -> None:
    """效应不可能超过失配率：这时应报错而不是给一个看似合理的配对数。"""

    with pytest.raises(PowerAnalysisError, match="大于失配率"):
        required_pairs(0.05, 0.10)
    with pytest.raises(PowerAnalysisError, match="大于失配率"):
        power_for(100, 0.05, 0.10)


@pytest.mark.parametrize(
    ("mismatch", "minimum_pairs"),
    [
        (0.10, 70),
        (0.15, 110),
        (0.20, 150),
        (0.30, 230),
        (0.40, 300),
    ],
)
def test_required_pairs_are_measured_and_stay_far_above_the_floor(
    mismatch: float, minimum_pairs: int
) -> None:
    """实测所需配对数说明"30 对下限远不足以检出 10 个百分点"。

    这正是计划强调"失配率低时计算值可能达到 100～200 对或更高"的原因，
    也是**不得**为适应预算把最小有意义效应从 10 改成 20 的理由。
    """

    result = required_pairs(mismatch, 0.10)
    assert result.pairs >= minimum_pairs
    assert result.power >= 0.80
    assert result.mismatch_rate == mismatch
    assert result.pairs > MIN_PAIRS_REQUIRED


def test_required_pairs_never_goes_below_the_floor() -> None:
    """30 对下限：即使计算值更小也不能低于它。"""

    result = required_pairs(0.40, 0.10)
    assert result.pairs >= MIN_PAIRS_REQUIRED or result.pairs == result.pairs
    assert result.pairs >= MIN_PAIRS_REQUIRED


def test_plan_for_family_multiplies_by_candidate_rounds() -> None:
    plan = plan_for_family(0.30, candidates=2)
    assert plan["candidates_k"] == 2
    assert plan["required_holdout_pairs"] == plan["planned_pairs"] * 2
    assert plan["floor_pairs"] == MIN_PAIRS_REQUIRED
    assert plan["achieved_power"] >= 0.80


def test_plan_for_family_rejects_zero_candidates() -> None:
    with pytest.raises(PowerAnalysisError, match="至少为 1"):
        plan_for_family(0.30, candidates=0)


def test_detectable_effect_reports_what_small_samples_can_show() -> None:
    """样本量受限时要能如实报出"只能检出至少 X 个百分点"。"""

    weak = detectable_effect(30, 0.30)
    strong = detectable_effect(300, 0.30)
    assert weak is not None and strong is not None
    assert weak > strong
    assert weak <= 0.30
    assert strong <= 0.10


def test_detectable_effect_is_none_when_no_effect_is_detectable() -> None:
    """检出力达不到目标时必须返回 None，不能拿失配率上限冒充"可检出"。

    30 对 + 失配率 10% 时，即使全部不一致对同向，检出力也只有约 7.3%：
    这时**没有**任何效应能在 80% 检出力下被检出，报告里不能出现一个百分比。
    """

    assert power_for(30, 0.10, 0.10) < 0.80
    assert detectable_effect(30, 0.10) is None
    assert maximum_power(30, 0.10) == pytest.approx(power_for(30, 0.10, 0.10))
    # 一旦样本量足够，同一个失配率下又能报出具体数值。
    assert detectable_effect(300, 0.10) is not None
    # 失配率越高越容易检出错：低失配率不可达时高失配率可能已经可达。
    assert maximum_power(30, 0.30) > maximum_power(30, 0.10)


def test_plan_for_family_exposes_max_power_for_unreachable_effects() -> None:
    plan = plan_for_family(0.10)
    assert plan["achieved_power"] >= 0.80
    assert plan["detectable_effect_at_planned_pairs"] == pytest.approx(0.10)
    assert plan["max_power_at_planned_pairs"] >= 0.80


def test_power_curve_covers_requested_effects_and_counts() -> None:
    rows = power_curve(0.30, effects=(0.10, 0.20), pair_counts=(30, 100, 300))
    assert {(row["pairs"], row["effect"]) for row in rows} == {
        (30, 0.10),
        (100, 0.10),
        (300, 0.10),
        (30, 0.20),
        (100, 0.20),
        (300, 0.20),
    }
    # 同一配对数下，效应越大检出力越高。
    for pairs in (30, 100, 300):
        small = next(
            row["power"] for row in rows if row["pairs"] == pairs and row["effect"] == 0.10
        )
        large = next(
            row["power"] for row in rows if row["pairs"] == pairs and row["effect"] == 0.20
        )
        assert large >= small
