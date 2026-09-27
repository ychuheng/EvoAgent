"""M6 S-03 冻结对照实验的测试：冻结检查、两臂顺序、精确判定与"不许多说"。

这些测试只验证**机制**：所有"结果"都是测试里构造的合成输入，用来检查判定逻辑；
任何一个判定都不构成 Skill 效果证据。（真实证据必须来自真实模型运行，标注 `real_model`。）
"""

from __future__ import annotations

import asyncio

import pytest

from evoagent.skills.experiment import (
    ARM_CONTROL,
    ARM_TREATMENT,
    MOCK,
    REAL_MODEL,
    ArmOutcome,
    FreezeError,
    PairOutcome,
    analyze_pairs,
    arm_order,
    assert_frozen,
    build_assignments,
    build_ledger_row,
    clopper_pearson,
    freeze_passed,
    plans_pairs_required,
    render_analysis,
    run_pairs,
    validate_freeze,
)

FAMILY = "code_comprehension"
DATASET = "m6-skill-holdout-v1.json"
MISMATCH = 0.10
PAIRS = plans_pairs_required(MISMATCH)  # 失配率 10% 时精确计算所得（78）


def make_manifest(count: int, *, used: int = 0, retired: int = 0) -> tuple[dict, list[str]]:
    """构造 count 条 m6-holdout 样本；前 used 条已有正式使用记录。"""

    samples = []
    ids = []
    for index in range(count):
        sample_id = f"m6-synth-{index:03d}"
        ids.append(sample_id)
        samples.append(
            {
                "sample_id": sample_id,
                "set": "m6-holdout",
                "task_family": FAMILY,
                "dataset": DATASET,
                "fixture": f"evals/fixtures/synth-{index:03d}",
                "fixture_hash": "0" * 64,
                "preregistration": "m0b-test",
                "first_formal_run": "run-0" if index < used else None,
                "retired_after_viewing": index < retired,
            }
        )
    return {"samples": samples}, ids


def make_spec(count: int, *, status: str = "frozen", budget: int = 5_000_000) -> dict:
    _, ids = make_manifest(count)
    return {
        "schema_version": 1,
        "status": status,
        "experiment_id": "m6-exp-test",
        "candidate_id": "skill-synth",
        "candidate_revision": "abc123",
        "task_family": FAMILY,
        "dataset": DATASET,
        "dataset_sha256": "f" * 64,
        "mismatch_rate_assumption": MISMATCH,
        "candidate_rounds": 1,
        "pairs_required": PAIRS,
        "seed": 20260927,
        "min_effect": 0.10,
        "arms": {
            ARM_CONTROL: {
                "skill_injection": "off",
                "model": "test-model",
                "tools_revision": "rev-1",
                "budget_micros": budget,
                "max_total_tokens": 50_000,
            },
            ARM_TREATMENT: {
                "skill_injection": "on",
                "model": "test-model",
                "tools_revision": "rev-1",
                "budget_micros": budget,
                "max_total_tokens": 50_000,
            },
        },
        "holdout_samples": ids,
    }


def check_by_name(spec: dict, *, manifest: dict | None = None, rows: list[dict] | None = None):
    if manifest is None:
        manifest, _ = make_manifest(len(spec["holdout_samples"]))
    checks = validate_freeze(spec, manifest=manifest, ledger_rows=rows or [])
    return {item.name: item for item in checks}


def outcome(
    sample_id: str,
    *,
    control: bool | None,
    treatment: bool | None,
    source: str = REAL_MODEL,
    violations: int = 0,
    control_tokens: int = 100,
    treatment_tokens: int = 120,
) -> PairOutcome:
    return PairOutcome(
        sample_id=sample_id,
        order=(ARM_CONTROL, ARM_TREATMENT),
        arms={
            ARM_CONTROL: ArmOutcome(
                arm=ARM_CONTROL,
                source=source,
                run_id=f"{sample_id}-c",
                success=control,
                wall_seconds=1.0,
                total_tokens=control_tokens,
                privilege_violations=violations,
            ),
            ARM_TREATMENT: ArmOutcome(
                arm=ARM_TREATMENT,
                source=source,
                run_id=f"{sample_id}-t",
                success=treatment,
                wall_seconds=1.0,
                total_tokens=treatment_tokens,
            ),
        },
    )


def paired_outcomes(
    pairs: int,
    *,
    discordant_control: int = 0,
    discordant_treatment: int = 0,
    both_success: int | None = None,
    source: str = REAL_MODEL,
    violations: int = 0,
) -> list[PairOutcome]:
    """构造 pairs 对结果：对照独成功、Skill 独成功、双双成功的数量可指定。"""

    both_success = (
        pairs - discordant_control - discordant_treatment if both_success is None else both_success
    )
    results: list[PairOutcome] = []
    index = 0
    for _ in range(both_success):
        results.append(outcome(f"s{index:03d}", control=True, treatment=True, source=source))
        index += 1
    for _ in range(discordant_control):
        results.append(
            outcome(
                f"s{index:03d}",
                control=True,
                treatment=False,
                source=source,
                violations=violations,
            )
        )
        index += 1
    for _ in range(discordant_treatment):
        results.append(outcome(f"s{index:03d}", control=False, treatment=True, source=source))
        index += 1
    assert len(results) == pairs
    return results


def test_arm_order_is_deterministic_and_balanced() -> None:
    orders = {arm_order(7, f"sample-{index}") for index in range(40)}
    assert orders == {(ARM_CONTROL, ARM_TREATMENT), (ARM_TREATMENT, ARM_CONTROL)}
    assert arm_order(7, "sample-1") == arm_order(7, "sample-1")
    assignments = build_assignments(3, ["a", "b", "c"])
    assert [item["sample_id"] for item in assignments] == ["a", "b", "c"]
    assert all(
        {item["first_arm"], item["second_arm"]} == {ARM_CONTROL, ARM_TREATMENT}
        for item in assignments
    )


def test_clopper_pearson_matches_published_values() -> None:
    """与 R `binom.test` 的等尾精确区间一致（3/10、5/5、6/0、0/6）。"""

    assert clopper_pearson(10, 3) == pytest.approx((0.06674, 0.65245), abs=1e-4)
    assert clopper_pearson(5, 5) == pytest.approx((0.47818, 1.0), abs=1e-4)
    assert clopper_pearson(6, 0) == pytest.approx((0.0, 0.45926), abs=1e-4)
    assert clopper_pearson(6, 6) == pytest.approx((0.54070, 1.0), abs=1e-4)
    with pytest.raises(ValueError):
        clopper_pearson(0, 0)


def test_freeze_passes_for_a_fully_frozen_spec() -> None:
    spec = make_spec(PAIRS)
    checks = check_by_name(spec)
    assert freeze_passed(list(checks.values())), [
        item.detail for item in checks.values() if not item.passed
    ]
    assert (
        assert_frozen(spec, manifest=make_manifest(PAIRS)[0], ledger_rows=[])[0]["sample_id"]
        == spec["holdout_samples"][0]
    )


def test_freeze_rejects_draft_status_and_placeholder_config() -> None:
    spec = make_spec(PAIRS, status="draft")
    spec["arms"][ARM_CONTROL]["model"] = "unset"
    spec["arms"][ARM_CONTROL]["budget_micros"] = 0
    checks = check_by_name(spec)
    assert not checks["spec_status_frozen"].passed
    assert not checks["arms_config_frozen"].passed
    assert "control.model" in checks["arms_config_frozen"].detail
    assert "control.budget_micros" in checks["arms_config_frozen"].detail

    with pytest.raises(FreezeError, match="冻结检查未通过"):
        assert_frozen(spec, manifest=make_manifest(PAIRS)[0], ledger_rows=[])


def test_freeze_rejects_arm_configuration_differences() -> None:
    """同模型、同工具、同预算是硬条件：任何一臂偷偷不同都不许跑。"""

    spec = make_spec(PAIRS)
    spec["arms"][ARM_TREATMENT]["budget_micros"] = 9_000_000
    checks = check_by_name(spec)
    assert not checks["arms_identical_except_skill"].passed
    assert "budget_micros" in checks["arms_identical_except_skill"].detail

    spec = make_spec(PAIRS)
    spec["arms"][ARM_TREATMENT]["model"] = "other-model"
    assert not check_by_name(spec)["arms_identical_except_skill"].passed

    spec = make_spec(PAIRS)
    spec["arms"][ARM_TREATMENT]["skill_injection"] = "off"
    assert not check_by_name(spec)["arms_identical_except_skill"].passed


def test_freeze_rejects_sample_size_below_power_calculation() -> None:
    """不得事后把 N 改小：预注册的 N 必须等于按失配率精确算出的值。"""

    spec = make_spec(PAIRS)
    spec["pairs_required"] = 30
    checks = check_by_name(spec)
    assert not checks["sample_size_matches_power_calculation"].passed
    assert str(PAIRS) in checks["sample_size_matches_power_calculation"].detail

    spec = make_spec(PAIRS)
    spec["pairs_required"] = 20
    assert not check_by_name(spec)["pairs_at_least_floor"].passed


def test_freeze_reports_exhausted_holdout_inventory() -> None:
    """库存不足时必须停下并暂停门槛判定，而不是拿 dev 或旧样本顶替。"""

    spec = make_spec(PAIRS)
    manifest, ids = make_manifest(PAIRS - 1)
    spec["holdout_samples"] = ids
    checks = check_by_name(spec, manifest=manifest)
    assert not checks["holdout_inventory_sufficient"].passed
    assert "暂停 M6 门槛判定" in checks["holdout_inventory_sufficient"].detail
    assert checks["holdout_samples_declared_from_stock"].passed


def test_freeze_rejects_reuse_and_declared_extra_samples() -> None:
    spec = make_spec(PAIRS)
    manifest, _ = make_manifest(PAIRS, used=1)
    checks = check_by_name(spec, manifest=manifest)
    assert not checks["no_holdout_reuse"].passed

    spec = make_spec(PAIRS)
    manifest, ids = make_manifest(PAIRS + 3)
    spec["holdout_samples"] = ids[:PAIRS]
    checks = check_by_name(spec, manifest=manifest)
    assert not checks["holdout_samples_declared_from_stock"].passed

    spec = make_spec(PAIRS)
    rows = [{"candidate_id": "skill-synth", "experiment_id": "m6-exp-test"}]
    checks = check_by_name(spec, rows=rows)
    assert not checks["no_holdout_reuse"].passed
    assert "候选 skill-synth 已有正式运行" in checks["no_holdout_reuse"].detail


def test_analysis_confirms_only_with_enough_real_pairs() -> None:
    outcomes = paired_outcomes(PAIRS, discordant_control=5, discordant_treatment=20)
    spec = make_spec(PAIRS)
    analysis = analyze_pairs(spec, outcomes)
    assert analysis.verdict == "positive_confirmed"
    assert analysis.comparable_pairs == PAIRS
    assert analysis.control_only == 5 and analysis.treatment_only == 20
    assert analysis.difference == pytest.approx(15 / PAIRS)
    assert analysis.exact_p_value < 0.05
    assert analysis.difference_ci is not None
    assert analysis.difference_ci[0] < analysis.difference < analysis.difference_ci[1]
    assert "未暴露 holdout" in analysis.claim
    assert analysis.control_tokens == PAIRS * 100
    assert analysis.treatment_tokens == PAIRS * 120


def test_analysis_refuses_mock_evidence() -> None:
    """mock/合成结果永远不能变成收益结论——这是本模块最重要的一条限制。"""

    outcomes = paired_outcomes(PAIRS, discordant_control=0, discordant_treatment=30, source=MOCK)
    analysis = analyze_pairs(make_spec(PAIRS), outcomes)
    assert analysis.verdict == "not_claimable"
    assert "不能作为效果证据" in analysis.reasons[0]
    assert "不构成效果证据" in analysis.claim


def test_analysis_downgrades_small_samples_to_exploratory() -> None:
    """样本不足时给探索性结论并写明可检出效应，不能声称收益。

    30 对 × 失配率 10% 时连"全部不一致对同向"都达不到 80% 检出力，
    因此这里必须如实写"没有任何效应可检出"，而不是给一个可检出效应的百分比。
    """

    outcomes = paired_outcomes(30, discordant_control=0, discordant_treatment=10)
    analysis = analyze_pairs(make_spec(PAIRS), outcomes)
    assert analysis.verdict == "exploratory_only"
    assert analysis.comparable_pairs == 30
    assert analysis.detectable_effect is None
    assert analysis.maximum_power < 0.80
    assert "没有任何效应" in analysis.claim
    assert analysis.required_pairs == PAIRS


def test_analysis_reports_detectable_effect_when_samples_allow_it() -> None:
    """失配率较高、样本量不足但仍有可检出效应时，报告必须写出这个效应大小。"""

    mismatch = 0.30
    spec = make_spec(PAIRS)
    spec["mismatch_rate_assumption"] = mismatch
    spec["pairs_required"] = plans_pairs_required(mismatch)
    outcomes = paired_outcomes(100, discordant_control=10, discordant_treatment=15)
    analysis = analyze_pairs(spec, outcomes)
    assert analysis.verdict == "exploratory_only"
    assert analysis.detectable_effect is not None
    assert "只能检出至少" in analysis.claim


def test_analysis_blocks_claims_on_safety_or_budget_violations() -> None:
    outcomes = paired_outcomes(PAIRS, discordant_control=5, discordant_treatment=20, violations=1)
    analysis = analyze_pairs(make_spec(PAIRS), outcomes)
    assert analysis.verdict == "not_claimable"
    assert analysis.privilege_violations == 5

    over_budget = [
        outcome(f"b{index:03d}", control=True, treatment=True, treatment_tokens=50_001)
        for index in range(PAIRS)
    ]
    analysis = analyze_pairs(make_spec(PAIRS), over_budget)
    assert analysis.over_budget_pairs == PAIRS
    assert analysis.verdict == "not_claimable"
    assert "超出冻结预算" in analysis.reasons[0]


def test_analysis_is_inconclusive_when_nothing_differs() -> None:
    outcomes = paired_outcomes(PAIRS, discordant_control=0, discordant_treatment=0)
    analysis = analyze_pairs(make_spec(PAIRS), outcomes)
    assert analysis.verdict == "inconclusive"
    assert analysis.difference_ci is None
    assert analysis.exact_p_value == 1.0
    assert "未达显著不等于 Skill 无效" in analysis.reasons[0]


def test_analysis_marks_unrecorded_arms_as_not_claimable() -> None:
    outcomes = paired_outcomes(PAIRS, discordant_control=1, discordant_treatment=9)
    outcomes.append(
        PairOutcome(
            sample_id="missing",
            order=(ARM_CONTROL, ARM_TREATMENT),
            arms={
                ARM_CONTROL: ArmOutcome(
                    arm=ARM_CONTROL,
                    source=REAL_MODEL,
                    run_id="missing-c",
                    success=None,
                    wall_seconds=0.0,
                    total_tokens=0,
                    errors=("runtime_error",),
                ),
                ARM_TREATMENT: ArmOutcome(
                    arm=ARM_TREATMENT,
                    source=REAL_MODEL,
                    run_id="missing-t",
                    success=False,
                    wall_seconds=1.0,
                    total_tokens=10,
                ),
            },
        )
    )
    analysis = analyze_pairs(make_spec(PAIRS), outcomes)
    assert analysis.verdict == "not_claimable"
    assert analysis.comparable_pairs == PAIRS
    assert "可比结果" in analysis.reasons[0]


def test_analysis_detects_negative_result() -> None:
    outcomes = paired_outcomes(PAIRS, discordant_control=20, discordant_treatment=2)
    analysis = analyze_pairs(make_spec(PAIRS), outcomes)
    assert analysis.verdict == "negative_confirmed"
    assert "禁用或回滚" in analysis.claim


def test_run_pairs_follows_frozen_order_and_records_everything() -> None:
    spec = make_spec(PAIRS)
    assignments = build_assignments(spec["seed"], ["a", "b", "c"])
    seen: list[tuple[str, str, int]] = []

    class Executor:
        async def run(self, *, sample_id: str, arm: str, order_index: int) -> ArmOutcome:
            seen.append((sample_id, arm, order_index))
            return ArmOutcome(
                arm=arm,
                source=MOCK,
                run_id=f"{sample_id}-{arm}",
                success=arm == ARM_TREATMENT,
                wall_seconds=0.5,
                total_tokens=42,
            )

    results = asyncio.run(run_pairs(spec, assignments, Executor()))
    assert [item.sample_id for item in results] == ["a", "b", "c"]
    for assignment, result in zip(assignments, results, strict=True):
        assert result.order == (assignment["first_arm"], assignment["second_arm"])
    for index, assignment in enumerate(assignments):
        assert seen[index * 2][0] == assignment["sample_id"]
        assert seen[index * 2][1] == assignment["first_arm"]
        assert seen[index * 2][2] == 0
        assert seen[index * 2 + 1][1] == assignment["second_arm"]
        assert seen[index * 2 + 1][2] == 1


def test_ledger_row_is_a_single_paired_formal_use() -> None:
    outcomes = paired_outcomes(4, discordant_control=1, discordant_treatment=2, both_success=1)
    row = build_ledger_row(make_spec(PAIRS), outcomes)
    assert row["milestone"] == "M6"
    assert row["run_id"] == "m6-exp-test-pair"
    assert row["model"] == "test-model"
    assert row["tools_revision"] == "rev-1"
    # 两臂用同一批样本、写在同一行：同一次配对实验属于同一次正式使用。
    assert row["arms"][ARM_CONTROL] == row["arms"][ARM_TREATMENT]
    assert len(row["arms"][ARM_CONTROL]) == 4
    assert len(row["run_ids"][ARM_TREATMENT]) == 4


def test_render_analysis_states_uncertainty_and_limits() -> None:
    outcomes = paired_outcomes(PAIRS, discordant_control=0, discordant_treatment=0)
    text = render_analysis(analyze_pairs(make_spec(PAIRS), outcomes), make_spec(PAIRS))
    assert "inconclusive" in text
    assert "不可估计" in text
    assert "精确配对检验 p=1.0000" in text
    assert "证明" not in text
