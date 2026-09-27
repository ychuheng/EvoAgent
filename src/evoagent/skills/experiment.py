"""M6 S-03：冻结对照实验的路径（预注册 → 冻结检查 → 配对记录 → 精确判定）。

计划 §11 S-03 原文：

> 使用 M0 未暴露、与开发 fixture 不同家族的 holdout；同模型、同工具、同预算、随机化两臂
> 顺序，记录任务成功、时间/token、错误与越权。
> 完成判据：留出集不参与改写；按下述样本量规则预注册，样本不足只给探索性结论。

这个模块只实现**机制**：把上面每一条变成可检查的数据结构与判断。它**不运行模型**，
也**不产生任何效果结论**——真实结果必须由真实模型运行产出并标注 `source="real_model"`；
凡是标注 `mock` 的结果一律判为「不可声称收益」，样本量不足时一律降级为探索性结论。
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field
from typing import Any, Protocol

from evoagent.skills.power import (
    DEFAULT_ALPHA,
    DEFAULT_MIN_EFFECT,
    DEFAULT_POWER,
    MIN_PAIRS_REQUIRED,
    PowerAnalysisError,
    detectable_effect,
    exact_p_value,
    maximum_power,
    required_pairs,
)

ARM_CONTROL = "control"
ARM_TREATMENT = "treatment"
ARMS = (ARM_CONTROL, ARM_TREATMENT)
HOLDOUT_SET = "m6-holdout"
SKILL_INJECTION_KEY = "skill_injection"
ARM_CONFIG_KEYS = ("model", "tools_revision", "budget_micros", "max_total_tokens")
REAL_MODEL = "real_model"
MOCK = "mock"

VERDICT_NOT_CLAIMABLE = "not_claimable"
VERDICT_EXPLORATORY = "exploratory_only"
VERDICT_POSITIVE = "positive_confirmed"
VERDICT_NEGATIVE = "negative_confirmed"
VERDICT_INCONCLUSIVE = "inconclusive"


class FreezeError(RuntimeError):
    """冻结检查不通过（例如 holdout 库存不足）；此时不得启动正式运行。"""


@dataclass(frozen=True, slots=True)
class FreezeCheck:
    name: str
    passed: bool
    detail: str


def _check(checks: list[FreezeCheck], name: str, passed: bool, detail: str) -> None:
    checks.append(FreezeCheck(name=name, passed=passed, detail=detail))


def arm_order(seed: int, sample_id: str) -> tuple[str, str]:
    """按 `(seed, sample_id)` 决定该样本的先跑臂——可复现、与结果无关。"""

    digest = hashlib.sha256(f"{seed}:{sample_id}".encode()).digest()
    if digest[0] % 2 == 0:
        return (ARM_CONTROL, ARM_TREATMENT)
    return (ARM_TREATMENT, ARM_CONTROL)


def build_assignments(seed: int, sample_ids: list[str]) -> list[dict[str, str]]:
    """为每条样本生成两臂顺序；同一实验重跑得到完全相同的顺序。"""

    assignments: list[dict[str, str]] = []
    for sample_id in sample_ids:
        first, second = arm_order(seed, sample_id)
        assignments.append({"sample_id": sample_id, "first_arm": first, "second_arm": second})
    return assignments


def available_holdout_samples(
    manifest: dict, *, task_family: str, dataset: str | None = None
) -> list[dict]:
    """台账里**仍可用**的 m6-holdout 样本：集合正确、家族匹配、未被正式使用、未曝光。"""

    available: list[dict] = []
    for sample in manifest.get("samples", []):
        if not isinstance(sample, dict):
            continue
        if sample.get("set") != HOLDOUT_SET:
            continue
        if sample.get("task_family") != task_family:
            continue
        if dataset is not None and sample.get("dataset") != dataset:
            continue
        if sample.get("first_formal_run") is not None:
            continue
        if sample.get("retired_after_viewing"):
            continue
        available.append(sample)
    return available


def plans_pairs_required(mismatch_rate: float) -> int:
    """按计划算出主要任务家族的配对数 `N`（30 与计算值的较大者）。"""

    return required_pairs(mismatch_rate, DEFAULT_MIN_EFFECT).pairs


def validate_freeze(
    spec: dict,
    *,
    manifest: dict,
    ledger_rows: list[dict],
) -> list[FreezeCheck]:
    """冻结检查：预注册参数、两臂一致性、样本量、库存、随机化。

    返回逐项结果；只要有一项不通过就**不得**启动正式运行（调用方用 `freeze_passed` 判定）。
    这些检查只覆盖 ID/哈希/台账层面的显式复用，不能证明两个 fixture 在语义上独立——
    那依赖 M0b 预注册时的人工抽查。
    """

    checks: list[FreezeCheck] = []

    required_fields = (
        "experiment_id",
        "candidate_id",
        "candidate_revision",
        "task_family",
        "dataset",
        "dataset_sha256",
        "mismatch_rate_assumption",
        "candidate_rounds",
        "pairs_required",
        "seed",
        "arms",
        "holdout_samples",
    )
    missing = [name for name in required_fields if spec.get(name) in (None, "", [], {})]
    _check(
        checks,
        "spec_fields_present",
        not missing,
        "预注册字段齐全" if not missing else f"缺少字段：{'、'.join(missing)}",
    )
    if missing:
        return checks

    family = spec["task_family"]
    pairs_required = spec["pairs_required"]
    rounds = spec["candidate_rounds"]

    # 草稿可以记录意图，但只有显式声明 `frozen` 的预注册才允许启动正式运行。
    _check(
        checks,
        "spec_status_frozen",
        spec.get("status") == "frozen",
        "预注册状态已冻结"
        if spec.get("status") == "frozen"
        else f"预注册状态是 {spec.get('status')!r}：草稿参数不得用于正式运行",
    )

    arms = spec["arms"]
    arms_ok = isinstance(arms, dict) and set(arms) == set(ARMS)
    if not arms_ok:
        _check(checks, "arms_complete", False, f"arms 必须恰好是 {list(ARMS)}")
        return checks
    _check(checks, "arms_complete", True, "两臂齐全")

    differences: list[str] = []
    for name in ARM_CONFIG_KEYS:
        values = {arm: arms[arm].get(name) for arm in ARMS}
        if len(set(map(repr, values.values()))) != 1:
            differences.append(f"{name}={values}")
    injections = {arm: arms[arm].get(SKILL_INJECTION_KEY) for arm in ARMS}
    if injections[ARM_CONTROL] != "off" or injections[ARM_TREATMENT] != "on":
        differences.append(f"{SKILL_INJECTION_KEY}={injections}")
    _check(
        checks,
        "arms_identical_except_skill",
        not differences,
        "同模型、同工具、同预算，只有 Skill 注入不同"
        if not differences
        else "两臂在冻结项上不一致：" + "；".join(differences),
    )

    frozen_missing: list[str] = []
    for arm in ARMS:
        config = arms[arm]
        model = config.get("model")
        if (
            not isinstance(model, str)
            or not model.strip()
            or model.strip().lower()
            in {
                "unset",
                "todo",
                "tbd",
            }
        ):
            frozen_missing.append(f"{arm}.model")
        tools = config.get("tools_revision")
        if not isinstance(tools, str) or not tools.strip():
            frozen_missing.append(f"{arm}.tools_revision")
        for key in ("budget_micros", "max_total_tokens"):
            value = config.get(key)
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                frozen_missing.append(f"{arm}.{key}")
    _check(
        checks,
        "arms_config_frozen",
        not frozen_missing,
        "模型、工具版本与预算额度都已冻结"
        if not frozen_missing
        else "以下冻结项仍是占位值或缺失，不得启动正式运行：" + "、".join(frozen_missing),
    )

    try:
        expected_pairs = plans_pairs_required(float(spec["mismatch_rate_assumption"]))
        power_error = None
    except (PowerAnalysisError, TypeError, ValueError) as error:
        expected_pairs = -1
        power_error = str(error)
    size_ok = power_error is None and int(pairs_required) == expected_pairs
    _check(
        checks,
        "sample_size_matches_power_calculation",
        size_ok,
        f"N={pairs_required} 与按失配率 {spec['mismatch_rate_assumption']} 精确计算一致"
        if size_ok
        else (
            f"预注册 N={pairs_required}，但按失配率 {spec['mismatch_rate_assumption']} "
            f"精确计算应为 {expected_pairs}（{power_error or '不得事后改小样本量'}）"
        ),
    )
    _check(
        checks,
        "pairs_at_least_floor",
        isinstance(pairs_required, int) and pairs_required >= MIN_PAIRS_REQUIRED,
        f"每个家族不少于 {MIN_PAIRS_REQUIRED} 对（当前声明 {pairs_required}）",
    )
    rounds_ok = isinstance(rounds, int) and rounds >= 1
    _check(checks, "candidate_rounds_positive", rounds_ok, f"计划候选轮数 K={rounds}")

    inventory_needed = int(pairs_required) * int(rounds) if isinstance(rounds, int) else -1
    dataset = spec["dataset"]
    stock = available_holdout_samples(manifest, task_family=family, dataset=dataset)
    declared = spec["holdout_samples"]
    stock_ids = {item.get("sample_id") for item in stock}
    undeclared_stock = sorted(stock_ids - set(declared)) if isinstance(declared, list) else []
    _check(
        checks,
        "holdout_inventory_sufficient",
        len(stock) >= inventory_needed,
        f"家族 {family} 可用 holdout {len(stock)} 条，计划需要 {inventory_needed} 条"
        + ("" if len(stock) >= inventory_needed else "：holdout 已用尽，按计划暂停 M6 门槛判定"),
    )
    _check(
        checks,
        "holdout_samples_declared_from_stock",
        isinstance(declared, list)
        and bool(declared)
        and set(declared) <= stock_ids
        and not undeclared_stock,
        "declare 的样本全部来自当前可用库存"
        if isinstance(declared, list) and not undeclared_stock
        else (
            f"预注册样本清单必须恰好用上当前可用库存；库存中有未声明的可用样本：{undeclared_stock}"
        ),
    )

    by_id = {sample.get("sample_id"): sample for sample in manifest.get("samples", [])}
    set_errors = [
        f"{sample_id}({by_id.get(sample_id, {}).get('set')})"
        for sample_id in (declared if isinstance(declared, list) else [])
        if by_id.get(sample_id, {}).get("set") != HOLDOUT_SET
    ]
    _check(
        checks,
        "holdout_set_membership",
        not set_errors,
        "全部样本属于 m6-holdout" if not set_errors else f"非 m6-holdout 样本：{set_errors}",
    )

    reused = [
        sample_id
        for sample_id in (declared if isinstance(declared, list) else [])
        if by_id.get(sample_id, {}).get("first_formal_run") is not None
    ]
    seen_candidates = {row.get("candidate_id") for row in ledger_rows if isinstance(row, dict)}
    seen_experiments = {row.get("experiment_id") for row in ledger_rows if isinstance(row, dict)}
    reuse_errors: list[str] = []
    if reused:
        reuse_errors.append(f"样本已有正式使用记录：{reused}")
    if spec["candidate_id"] in seen_candidates:
        reuse_errors.append(f"候选 {spec['candidate_id']} 已有正式运行")
    if spec["experiment_id"] in seen_experiments:
        reuse_errors.append(f"experiment_id {spec['experiment_id']} 已在台账中")
    _check(
        checks,
        "no_holdout_reuse",
        not reuse_errors,
        "留出集未被使用过" if not reuse_errors else "；".join(reuse_errors),
    )

    seed = spec["seed"]
    assignments = (
        build_assignments(seed, list(declared))
        if isinstance(seed, int) and isinstance(declared, list)
        else []
    )
    seeded_ok = (
        isinstance(seed, int)
        and len(assignments) == len(declared)
        and all(set(item.values()) >= {ARM_CONTROL, ARM_TREATMENT} for item in assignments)
    )
    _check(
        checks,
        "arm_order_randomized_and_recorded",
        seeded_ok,
        f"以 seed={seed} 逐样本固定两臂顺序，可复现且与结果无关",
    )
    return checks


def freeze_passed(checks: list[FreezeCheck]) -> bool:
    return all(item.passed for item in checks)


def assert_frozen(spec: dict, *, manifest: dict, ledger_rows: list[dict]) -> list[dict[str, str]]:
    """冻结通过则返回两臂顺序；否则抛出带全部失败项的错误。"""

    checks = validate_freeze(spec, manifest=manifest, ledger_rows=ledger_rows)
    if not freeze_passed(checks):
        failed = "；".join(item.detail for item in checks if not item.passed)
        raise FreezeError(f"冻结检查未通过：{failed}")
    return build_assignments(spec["seed"], list(spec["holdout_samples"]))


@dataclass(frozen=True, slots=True)
class ArmOutcome:
    """一臂在一条样本上的记录：成功、时间、token、错误、越权。"""

    arm: str
    source: str
    run_id: str
    success: bool | None
    wall_seconds: float
    total_tokens: int
    errors: tuple[str, ...] = ()
    privilege_violations: int = 0

    def __post_init__(self) -> None:
        if self.arm not in ARMS:
            raise ValueError(f"未知臂名：{self.arm}")
        if self.source not in {REAL_MODEL, MOCK}:
            raise ValueError(f"source 必须是 {REAL_MODEL} 或 {MOCK}")
        if self.success is None and not self.errors:
            raise ValueError("成功与否未知时必须记录错误原因")


@dataclass(frozen=True, slots=True)
class PairOutcome:
    sample_id: str
    order: tuple[str, str]
    arms: dict[str, ArmOutcome]

    def __post_init__(self) -> None:
        if set(self.arms) != set(ARMS):
            raise ValueError("一条配对记录必须同时包含 control 与 treatment 两臂")
        if set(self.order) != set(ARMS) or len(self.order) != 2:
            raise ValueError("order 必须恰好列出两条臂各一次")

    @property
    def sources(self) -> set[str]:
        return {outcome.source for outcome in self.arms.values()}


@dataclass(frozen=True, slots=True)
class PairAnalysis:
    pairs: int
    comparable_pairs: int
    both_success: int
    both_failure: int
    control_only: int
    treatment_only: int
    control_success_rate: float
    treatment_success_rate: float
    difference: float
    difference_ci: tuple[float, float] | None
    exact_p_value: float
    alpha: float
    min_effect: float
    required_pairs: int
    detectable_effect: float | None
    maximum_power: float
    control_tokens: int
    treatment_tokens: int
    control_seconds: float
    treatment_seconds: float
    error_count: int
    privilege_violations: int
    over_budget_pairs: int
    verdict: str
    reasons: tuple[str, ...] = field(default=())
    claim: str = ""


def _binomial_upper_tail(k: int, d: int, q: float) -> float:
    """`P(X >= d)`，`X ~ Binomial(k, q)`（`k` 很小，直接求和足够精确）。"""

    if d <= 0:
        return 1.0
    if d > k:
        return 0.0
    return sum(math.comb(k, index) * q**index * (1 - q) ** (k - index) for index in range(d, k + 1))


def _binomial_lower_tail(k: int, d: int, q: float) -> float:
    if d < 0:
        return 0.0
    if d >= k:
        return 1.0
    return sum(math.comb(k, index) * q**index * (1 - q) ** (k - index) for index in range(0, d + 1))


def _bisect_probability(k: int, d: int, target: float, *, upper: bool) -> float:
    """解 `P(X >= d) = target`（upper）或 `P(X <= d) = target`（lower）。

    `P(X >= d)` 关于 `q` 单调不减，`P(X <= d)` 关于 `q` 单调不增；二分方向必须与
    单调性一致，否则会收敛到错误的端点（曾因此把区间压成一个极小的同值点）。
    """

    low, high = 0.0, 1.0
    for _ in range(200):
        middle = (low + high) / 2
        if upper:
            # 要让 P(X>=d) 变大；值偏小说明 q 取小了。
            if _binomial_upper_tail(k, d, middle) < target:
                low = middle
            else:
                high = middle
        else:
            # 要让 P(X<=d) 变小；值偏大说明 q 取小了。
            if _binomial_lower_tail(k, d, middle) > target:
                low = middle
            else:
                high = middle
    return (low + high) / 2


def clopper_pearson(k: int, successes: int, *, alpha: float = DEFAULT_ALPHA) -> tuple[float, float]:
    """`successes / k` 的精确二项置信区间（Clopper–Pearson），用二分法对精确尾概率求逆。"""

    if k <= 0:
        raise ValueError("不一致对数为 0 时没有可估计的比例")
    if not 0 <= successes <= k:
        raise ValueError("successes 必须落在 [0, k]")
    # 等尾 Clopper–Pearson：下界解 `P(X >= x) = alpha/2`，上界解 `P(X <= x) = alpha/2`。
    # x=0 时下界为 0、x=k 时上界为 1，与 `binom.test` 一致（例如 3/10 → 0.0667~0.6525）。
    lower = 0.0 if successes == 0 else _bisect_probability(k, successes, alpha / 2, upper=True)
    upper = 1.0 if successes == k else _bisect_probability(k, successes, alpha / 2, upper=False)
    return (lower, upper)


def _paired_difference_ci(
    control_only: int, treatment_only: int, pairs: int, *, alpha: float
) -> tuple[float, float] | None:
    """配对差异的精确条件置信区间：由不一致对中偏向 treatment 的比例反推。"""

    discordant = control_only + treatment_only
    if discordant == 0 or pairs == 0:
        return None
    low, high = clopper_pearson(discordant, treatment_only, alpha=alpha)
    return ((2 * low - 1) * discordant / pairs, (2 * high - 1) * discordant / pairs)


def analyze_pairs(
    spec: dict,
    outcomes: list[PairOutcome],
    *,
    alpha: float = DEFAULT_ALPHA,
    target_power: float = DEFAULT_POWER,
) -> PairAnalysis:
    """按计划给出配对差异与不确定性；样本量不足或证据非真实模型时**不声称收益**。"""

    min_effect = float(spec.get("min_effect", DEFAULT_MIN_EFFECT))
    mismatch = float(spec["mismatch_rate_assumption"])
    pairs = len(outcomes)
    comparable = 0
    both_success = both_failure = control_only = treatment_only = 0
    control_tokens = treatment_tokens = 0
    control_seconds = treatment_seconds = 0.0
    error_count = 0
    violations = 0
    over_budget = 0
    non_real: list[str] = []
    budget = {arm: spec["arms"][arm].get("max_total_tokens") for arm in ARMS if "arms" in spec}

    for outcome in outcomes:
        control = outcome.arms[ARM_CONTROL]
        treatment = outcome.arms[ARM_TREATMENT]
        control_tokens += control.total_tokens
        treatment_tokens += treatment.total_tokens
        control_seconds += control.wall_seconds
        treatment_seconds += treatment.wall_seconds
        error_count += len(control.errors) + len(treatment.errors)
        violations += control.privilege_violations + treatment.privilege_violations
        if outcome.sources != {REAL_MODEL}:
            non_real.append(outcome.sample_id)
        for arm, item in ((ARM_CONTROL, control), (ARM_TREATMENT, treatment)):
            limit = budget.get(arm)
            if isinstance(limit, int) and limit > 0 and item.total_tokens > limit:
                over_budget += 1
        if control.success is None or treatment.success is None:
            continue
        comparable += 1
        if control.success and treatment.success:
            both_success += 1
        elif not control.success and not treatment.success:
            both_failure += 1
        elif control.success and not treatment.success:
            control_only += 1
        else:
            treatment_only += 1

    difference = 0.0
    control_success_rate = treatment_success_rate = 0.0
    if comparable:
        control_success_rate = (both_success + control_only) / comparable
        treatment_success_rate = (both_success + treatment_only) / comparable
        difference = treatment_success_rate - control_success_rate

    discordant = control_only + treatment_only
    p_value = exact_p_value(discordant, min(control_only, treatment_only))
    ci = _paired_difference_ci(control_only, treatment_only, comparable, alpha=alpha)
    try:
        planned = required_pairs(mismatch, min_effect, alpha=alpha, target_power=target_power).pairs
    except PowerAnalysisError:
        planned = MIN_PAIRS_REQUIRED
    detected = detectable_effect(comparable, mismatch, alpha=alpha, target_power=target_power)
    top_power = maximum_power(comparable, mismatch, alpha=alpha) if comparable else 0.0

    reasons: list[str] = []
    verdict = VERDICT_INCONCLUSIVE
    if non_real:
        verdict = VERDICT_NOT_CLAIMABLE
        reasons.append(
            "以下样本的结果不是真实模型运行（"
            + "、".join(non_real)
            + "）；mock/合成结果不能作为效果证据"
        )
    if comparable != pairs:
        verdict = VERDICT_NOT_CLAIMABLE
        reasons.append(f"{pairs - comparable} 对缺少年内可比结果（成功与否未知），先补齐记录")
    if over_budget:
        verdict = VERDICT_NOT_CLAIMABLE
        reasons.append(f"{over_budget} 臂超出冻结预算上限，这次比较已不是预注册的对照")
    if violations:
        verdict = VERDICT_NOT_CLAIMABLE
        reasons.append(f"记录到 {violations} 次越权，安全退化必须先行处理")
    if verdict != VERDICT_NOT_CLAIMABLE:
        if comparable == 0:
            verdict = VERDICT_INCONCLUSIVE
            reasons.append("没有任何可比配对，无法给出结论")
        elif comparable < planned:
            verdict = VERDICT_EXPLORATORY
            reasons.append(
                f"实际 {comparable} 对少于预注册的 {planned} 对：只能报告探索性结果，"
                + (
                    f"本样本量在 {target_power:.0%} 检出力下只能检出至少 {detected:.1%} 的绝对提升"
                    if detected is not None
                    else f"且本样本量下最高检出力仅 {top_power:.1%}，没有任何效应可检出"
                )
            )
        elif p_value <= alpha and difference >= min_effect:
            verdict = VERDICT_POSITIVE
            reasons.append(
                f"精确配对检验 p={p_value:.4f} ≤ {alpha} 且配对差异 {difference:.1%} ≥ "
                f"最小有意义效应 {min_effect:.0%}"
            )
        elif p_value <= alpha and difference <= -min_effect:
            verdict = VERDICT_NEGATIVE
            reasons.append(f"精确配对检验 p={p_value:.4f} ≤ {alpha} 且 Skill 臂显著更差")
        else:
            verdict = VERDICT_INCONCLUSIVE
            reasons.append(
                f"精确配对检验 p={p_value:.4f} > {alpha}：**未达显著不等于 Skill 无效**，"
                "按计划如实报告为无结论"
            )

    claim = _claim_text(verdict, comparable, difference, ci, p_value, detected)
    return PairAnalysis(
        pairs=pairs,
        comparable_pairs=comparable,
        both_success=both_success,
        both_failure=both_failure,
        control_only=control_only,
        treatment_only=treatment_only,
        control_success_rate=control_success_rate,
        treatment_success_rate=treatment_success_rate,
        difference=difference,
        difference_ci=ci,
        exact_p_value=p_value,
        alpha=alpha,
        min_effect=min_effect,
        required_pairs=planned,
        detectable_effect=detected,
        maximum_power=top_power,
        control_tokens=control_tokens,
        treatment_tokens=treatment_tokens,
        control_seconds=control_seconds,
        treatment_seconds=treatment_seconds,
        error_count=error_count,
        privilege_violations=violations,
        over_budget_pairs=over_budget,
        verdict=verdict,
        reasons=tuple(reasons),
        claim=claim,
    )


def _claim_text(
    verdict: str,
    comparable: int,
    difference: float,
    ci: tuple[float, float] | None,
    p_value: float,
    detected: float | None,
) -> str:
    if verdict == VERDICT_POSITIVE:
        interval = f"[{ci[0]:.1%}, {ci[1]:.1%}]" if ci else "不可估计"
        return (
            f"在 {comparable} 对未暴露 holdout 上，Skill 臂成功率高出 {difference:.1%}"
            f"（配对差异 95% 区间 {interval}，精确配对检验 p={p_value:.4f}）"
        )
    if verdict == VERDICT_NEGATIVE:
        return f"在 {comparable} 对上 Skill 臂显著更差（配对差异 {difference:.1%}）：应禁用或回滚"
    if verdict == VERDICT_EXPLORATORY:
        detail = (
            f"本样本量在 80% 检出力下只能检出至少 {detected:.1%} 的绝对提升"
            if detected is not None
            else "本样本量下没有任何效应能在 80% 检出力下被检出"
        )
        return f"探索性结果（{comparable} 对）：观测到配对差异 {difference:.1%}；{detail}"
    if verdict == VERDICT_NOT_CLAIMABLE:
        return "不构成效果证据：冻结条件或证据来源不满足，先修正后再谈收益"
    return f"无结论（{comparable} 对，配对差异 {difference:.1%}，p={p_value:.4f}）"


class PairExecutor(Protocol):
    """真实运行的接入点：由调用方在**真实模型、同工具、同预算**下实现。"""

    async def run(self, *, sample_id: str, arm: str, order_index: int) -> ArmOutcome: ...


async def run_pairs(
    spec: dict, assignments: list[dict[str, str]], executor: PairExecutor
) -> list[PairOutcome]:
    """按冻结顺序逐对执行两臂。执行器返回什么就记录什么，不做任何推断。"""

    del spec  # 顺序来自 assignments（已由冻结检查产出并记录），这里不再读 spec。
    results: list[PairOutcome] = []
    for item in assignments:
        order = (item["first_arm"], item["second_arm"])
        arms: dict[str, ArmOutcome] = {}
        for index, arm in enumerate(order):
            arms[arm] = await executor.run(sample_id=item["sample_id"], arm=arm, order_index=index)
        results.append(PairOutcome(sample_id=item["sample_id"], order=order, arms=arms))
    return results


def build_ledger_row(spec: dict, outcomes: list[PairOutcome]) -> dict[str, Any]:
    """正式使用台账行：一次配对实验写一行，两臂视为同一次正式使用。"""

    arms = {arm: [] for arm in ARMS}
    for outcome in outcomes:
        arms[ARM_CONTROL].append(outcome.arms[ARM_CONTROL].run_id)
        arms[ARM_TREATMENT].append(outcome.arms[ARM_TREATMENT].run_id)
    return {
        "run_id": f"{spec['experiment_id']}-pair",
        "experiment_id": spec["experiment_id"],
        "candidate_id": spec["candidate_id"],
        "candidate_revision": spec["candidate_revision"],
        "model": spec["arms"][ARM_CONTROL]["model"],
        "tools_revision": spec["arms"][ARM_CONTROL]["tools_revision"],
        "dataset_sha256": spec["dataset_sha256"],
        "milestone": "M6",
        "arms": {
            ARM_CONTROL: [outcome.sample_id for outcome in outcomes],
            ARM_TREATMENT: [outcome.sample_id for outcome in outcomes],
        },
        "run_ids": arms,
    }


def render_analysis(analysis: PairAnalysis, spec: dict) -> str:
    """把判定渲染成可粘贴进报告的 Markdown（含不确定性，不使用「证明」字样）。"""

    ci = analysis.difference_ci
    lines = [
        f"## {spec['experiment_id']}",
        "",
        f"家族 `{spec['task_family']}`，候选 `{spec['candidate_id']}`。",
        "",
        f"- 判定：**{analysis.verdict}**",
        f"- 配对差异（Skill 减对照）：{analysis.difference:+.1%}，95% 精确条件区间 "
        + (f"[{ci[0]:+.1%}, {ci[1]:+.1%}]" if ci else "不可估计（无不一致对）"),
        f"- 不一致对：对照独成功 {analysis.control_only}，Skill 独成功 {analysis.treatment_only}；"
        f"精确配对检验 p={analysis.exact_p_value:.4f}（α={analysis.alpha}）",
        f"- 样本量：实际可比 {analysis.comparable_pairs} 对 / 预注册 {analysis.required_pairs} 对；"
        + (
            f"本样本量可检出 ≥{analysis.detectable_effect:.1%}"
            if analysis.detectable_effect is not None
            else f"本样本量最高检出力 {analysis.maximum_power:.1%}"
        ),
        f"- 资源：对照 {analysis.control_tokens} token / {analysis.control_seconds:.1f}s；"
        f"Skill {analysis.treatment_tokens} token / {analysis.treatment_seconds:.1f}s",
        f"- 错误 {analysis.error_count} 条，越权 {analysis.privilege_violations} 次，"
        f"超预算臂记录 {analysis.over_budget_pairs} 条",
        "",
        f"> {analysis.claim}",
        "",
    ]
    if analysis.reasons:
        lines.append("限制与理由：")
        lines.extend(f"- {reason}" for reason in analysis.reasons)
        lines.append("")
    return "\n".join(lines)
