"""dev 失配率换算的测试：配对计数、可行性判定与"零失配不能缩小样本量"。"""

from __future__ import annotations

import pytest

from evoagent.skills.power import MIN_PAIRS_REQUIRED
from scripts.m6_dev_mismatch import mismatch_rate, plan_from_measured


def pairs(*flags: tuple[bool, bool]) -> list[dict]:
    return [
        {"dev_case": f"case-{index}", "control_success": left, "treatment_success": right}
        for index, (left, right) in enumerate(flags)
    ]


def test_mismatch_counts_every_pair_class() -> None:
    measured = mismatch_rate(
        pairs((True, True), (False, False), (True, False), (False, True), (True, False))
    )
    assert measured == {
        "pairs": 5,
        "both_success": 1,
        "both_failure": 1,
        "control_only": 2,
        "treatment_only": 1,
        "discordant": 3,
        "mismatch_rate": pytest.approx(0.6),
    }


def test_mismatch_requires_boolean_verdicts() -> None:
    with pytest.raises(ValueError):
        mismatch_rate([{"control_success": True}])
    with pytest.raises(ValueError):
        mismatch_rate([])


def test_zero_mismatch_must_not_shrink_the_sample_size() -> None:
    """两臂完全一致时不能"样本量够小"——那说明样本区分不出效果或两臂配置相同。"""

    plan = plan_from_measured(0.0)
    assert plan["feasible"] is False
    assert "不能" in plan["reason"]
    assert "planned_pairs" not in plan
    assert plan["floor_pairs"] == MIN_PAIRS_REQUIRED


def test_mismatch_below_effect_is_infeasible() -> None:
    plan = plan_from_measured(0.05, effect=0.10)
    assert plan["feasible"] is False
    assert "不可能" in plan["reason"]


def test_feasible_mismatch_yields_pairs_and_inventory() -> None:
    plan = plan_from_measured(0.10, candidates=2)
    assert plan["feasible"] is True
    assert plan["planned_pairs"] == 78
    assert plan["required_holdout_pairs"] == 156
    assert plan["candidates_k"] == 2
    assert plan["achieved_power"] >= 0.80
    assert "预注册" in plan["note"]


def test_invalid_inputs_are_rejected() -> None:
    with pytest.raises(ValueError):
        plan_from_measured(0.2, candidates=0)
    with pytest.raises(ValueError):
        plan_from_measured(1.5)
