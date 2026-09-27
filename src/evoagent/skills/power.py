"""M6 样本量：配对二分类检验的精确功效计算与可检出效应。

计划 §11「样本量与预算先后」要求：

- 选定最小有意义效应（**任务成功率绝对提升至少 10 个百分点**）；
- 从 dev 试跑估计配对两臂的**失配率**；
- 用配对二分类检验的**精确计算或模拟**求双侧显著性水平 0.05、至少 80% 检出力所需的配对数；
- **每个要单独声称有效的任务家族不得少于 30 对**，实际计划数取 30 与计算值中的较大者。

模型（为什么只用不一致对）：

一次配对实验里，两臂结果**相同**的样本不提供关于差异的任何信息。设失配率 `m`
（一臂成功、另一臂失败的比例），不一致对数 `K ~ Binomial(N, m)`。给定 `K`，
统计量是"不一致对中 treatment 成功"的个数 `D`：在零假设下 `D ~ Binomial(K, 0.5)`
（无效应时两臂对称），在最小有意义效应 `delta` 下 `D ~ Binomial(K, q1)`，
其中 `q1 = 0.5 + delta / (2 m)`（需要 `delta <= m`，否则该效应在给定失配率下不可达）。

精确检验与并列的处理：

双侧精确拒绝域用"p 值法"构造：`p(d) = P(X <= d) + P(X >= K - d)`（`X ~ Binomial(K, 0.5)`，
"至少这么极端"的定义），拒绝所有 `p(d) <= alpha` 的 `d`。这会让 `d = 0` 与 `d = K`
（结果完全一致，只来自 `P(X <= 0)` 这一项、没算另一侧）**不构成拒绝**——这正是
"全一致不算显著"的体现。若改用"单侧尾概率 <= alpha/2"的老式构造，`K=0` 或 `K=1`
这种退化情形会被当成拒绝，从而把所需配对数算小好几倍。

`required_pairs` 用正态近似定位搜索窗口，但在窗口内逐个做精确计算，因此给出的仍是精确值。
"""

from __future__ import annotations

import math
from dataclasses import dataclass

DEFAULT_ALPHA = 0.05
DEFAULT_POWER = 0.80
DEFAULT_MIN_EFFECT = 0.10
# 计划要求的下限：防止极小样本，但不保证检出 10 个百分点。
MIN_PAIRS_REQUIRED = 30
MAX_PAIRS_SEARCH = 5_000
# 对精确检验做数值积分时，忽略权重低于该值的 K（对精度影响远小于报告位数）。
MIN_WEIGHT = 1e-14


class PowerAnalysisError(ValueError):
    """参数不成立（例如效应大于失配率）。"""


@dataclass(frozen=True, slots=True)
class PowerResult:
    pairs: int
    mismatch_rate: float
    effect: float
    alpha: float
    power: float
    expected_discordant: float
    reject_upper: int
    reject_lower: int


def _log_binomial_pmf(k: int, n: int, p: float) -> float:
    """用对数域计算二项概率，避免大 n 时中间量溢出。"""

    if p <= 0.0:
        return 0.0 if k == 0 else -math.inf
    if p >= 1.0:
        return 0.0 if k == n else -math.inf
    if k < 0 or k > n:
        return -math.inf
    return (
        math.lgamma(n + 1)
        - math.lgamma(k + 1)
        - math.lgamma(n - k + 1)
        + k * math.log(p)
        + (n - k) * math.log1p(-p)
    )


def _binomial_pmf(k: int, n: int, p: float) -> float:
    value = _log_binomial_pmf(k, n, p)
    return 0.0 if value == -math.inf else math.exp(value)


def _cdf_terms(n: int, r: int, p: float) -> float:
    """`P(X <= r)`，`X ~ Binomial(n, p)`；`p == 1` 等边界也安全。"""

    if r < 0:
        return 0.0
    if r >= n:
        return 1.0
    if p <= 0.0:
        return 1.0
    if p >= 1.0:
        return 0.0
    return sum(_binomial_pmf(index, n, p) for index in range(0, r + 1))


def _upper_tail(n: int, r: int, p: float) -> float:
    """`P(X > r)`；用"1 减去下尾"实现，避免 `p / (1 - p)` 在 `p == 1` 时除零。"""

    if r >= n:
        return 0.0
    if r < 0:
        return 1.0
    return max(0.0, min(1.0, 1.0 - _cdf_terms(n, r, p)))


def _lower_tail(k: int, d: int) -> float:
    """`P(X <= d)`，`X ~ Binomial(k, 0.5)`。"""

    if d < 0:
        return 0.0
    if d >= k:
        return 1.0
    return sum(_binomial_pmf(index, k, 0.5) for index in range(0, d + 1))


def exact_p_value(k: int, d: int) -> float:
    """`K` 个不一致对里有 `d` 个偏向 treatment 时的双侧精确 p 值。

    标准定义：`p = 2 * min(P(X <= d), P(X >= d))`（`X ~ Binomial(k, 0.5)`），上限为 1。
    **不能**用"P(X<=d) + P(X>=K-d)"：那个量在极端处反而变成 1，会把"全部偏向一侧"
    判成不显著，方向正好反了。
    """

    if k <= 0:
        return 1.0
    lower = _lower_tail(k, d)
    upper = 1.0 - _lower_tail(k, d - 1)
    return min(1.0, 2 * min(lower, upper))


def rejection_bounds(k: int, *, alpha: float = DEFAULT_ALPHA) -> tuple[int, int] | None:
    """给定不一致对数 `k` 的双侧精确拒绝域：`[0, lower-1] ∪ [upper+1, k]`。

    返回 `None` 表示该 `k` 下没有任何结果能在 `alpha` 水平被拒绝：
    `k=5` 时 `p(0)=0.0625` 不可以，`k=6` 时 `p(0)=0.031` 可以。
    判定按"最可能的结果优先"累积概率，找到使累积概率首次超过 `alpha/2` 的位置。
    """

    if k <= 0:
        return None
    if exact_p_value(k, 0) > alpha:
        return None
    order = [k, *range(0, k)]
    cumulative = 0.0
    threshold = alpha / 2
    # 累积概率首次超过 alpha/2 时，当前结果本身已经"够可能"，接受域从它开始。
    # 注意索引：order[0] 是 k（接受域右端），order[1] 是 0（接受域左端）。
    lower = 0
    for outcome in order:
        cumulative += _binomial_pmf(outcome, k, 0.5)
        if cumulative > threshold:
            lower = outcome
            break
    if lower == 0:
        # 单侧极端已经显著（k 较小时会出现）：拒绝域是 {0, k}，接受域 [1, k-1]。
        return (1, k - 1) if k >= 2 else None
    upper = k - lower
    if upper >= k:
        return None
    return lower, upper


def conditional_power(k: int, q: float, *, alpha: float = DEFAULT_ALPHA) -> float:
    """给定 `k` 个不一致对与"偏向 treatment 的概率" `q` 时的条件功效。"""

    bounds = rejection_bounds(k, alpha=alpha)
    if bounds is None:
        return 0.0
    lower, upper = bounds
    # 拒绝域是 [0, lower-1] ∪ [upper+1, k]。
    left = 0.0 if lower <= 0 else sum(_binomial_pmf(index, k, q) for index in range(0, lower))
    right = _upper_tail(k, upper, q)
    return min(1.0, left + right)


def power_for(
    pairs: int,
    mismatch_rate: float,
    effect: float,
    *,
    alpha: float = DEFAULT_ALPHA,
) -> float:
    """给定配对数、失配率与效应，计算精确检验的功效。"""

    if not 0.0 < mismatch_rate <= 1.0:
        raise PowerAnalysisError("失配率必须在 (0, 1] 区间")
    if effect < 0.0:
        raise PowerAnalysisError("效应不能为负")
    if effect > mismatch_rate:
        raise PowerAnalysisError(
            f"效应 {effect:.3f} 大于失配率 {mismatch_rate:.3f}：在只靠不一致对提供信息的"
            "配对检验里这不可能实现，请重新考虑任务家族或最小有意义效应"
        )
    if effect == 0.0:
        return alpha
    if pairs < 1:
        raise PowerAnalysisError("配对数必须为正")

    q1 = 0.5 + effect / (2 * mismatch_rate)
    # 只在权重不可忽略的区间内求和；两侧各留出足够多的标准差。
    spread = 6 * math.sqrt(pairs * mismatch_rate * (1 - mismatch_rate)) + 6
    low = max(0, int(pairs * mismatch_rate - spread))
    high = min(pairs, int(pairs * mismatch_rate + spread) + 1)

    total = 0.0
    weight = _binomial_pmf(low, pairs, mismatch_rate)
    ratio = mismatch_rate / (1 - mismatch_rate) if mismatch_rate < 1 else math.inf
    for k in range(low, high + 1):
        if k > low:
            weight = (
                weight * ((pairs - k + 1) / k) * ratio
                if mismatch_rate < 1
                else (1.0 if k == pairs else 0.0)
            )
        if weight < MIN_WEIGHT:
            continue
        total += weight * conditional_power(k, q1, alpha=alpha)
    return min(total, 1.0)


def _normal_quantile(p: float) -> float:
    """标准正态分位数（Acklam 有理逼近；只用于定位精确搜索窗口）。"""

    if not 0.0 < p < 1.0:
        raise PowerAnalysisError("分位点必须在 (0, 1) 内")
    a = (
        -3.969683028665376e01,
        2.209460984245205e02,
        -2.759285104469687e02,
        1.383577518672690e02,
        -3.066479806614716e01,
        2.506628277459239e00,
    )
    b = (
        -5.447609879822406e01,
        1.615858368580409e02,
        -1.556989798598866e02,
        6.680131188771972e01,
        -1.328068155288572e01,
    )
    c = (
        -7.784894002430293e-03,
        -3.223964580411365e-01,
        -2.400758277161838e00,
        -2.549732539343734e00,
        4.374664141464968e00,
        2.938163982698783e00,
    )
    d = (
        7.784695709041462e-03,
        3.224671290700398e-01,
        2.445134137142996e00,
        3.754408661907416e00,
    )
    plow, phigh = 0.02425, 1 - 0.02425
    if p < plow:
        q = math.sqrt(-2 * math.log(p))
        return ((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5] / (
            (((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1
        )
    if p > phigh:
        q = math.sqrt(-2 * math.log(1 - p))
        return -((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5] / (
            (((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1
        )
    q = p - 0.5
    r = q * q
    return ((((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q) / (
        ((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1
    )


def _approximate_pairs(
    mismatch_rate: float, effect: float, *, alpha: float, target_power: float
) -> int:
    """正态近似下的配对数，用来把精确搜索限制在窄窗口内。

    近似式 `n = (z_{1-a/2} * m + z_power * sqrt(m^2 - delta^2))^2 / delta^2`
    （`m` 为失配率、`delta` 为绝对效应）。它只负责**定位**，最终数值仍由精确计算给出。
    """

    z_alpha = _normal_quantile(1 - alpha / 2)
    z_power = _normal_quantile(target_power)
    numerator = (
        z_alpha * mismatch_rate + z_power * math.sqrt(max(mismatch_rate**2 - effect**2, 0.0))
    ) ** 2
    return max(1, int(math.ceil(numerator / (effect**2))))


def required_pairs(
    mismatch_rate: float,
    effect: float = DEFAULT_MIN_EFFECT,
    *,
    alpha: float = DEFAULT_ALPHA,
    target_power: float = DEFAULT_POWER,
    floor: int = MIN_PAIRS_REQUIRED,
) -> PowerResult:
    """求达到目标检出力所需的最小配对数（已应用 30 对下限）。"""

    if effect > mismatch_rate:
        raise PowerAnalysisError(
            f"最小有意义效应 {effect:.3f} 大于失配率 {mismatch_rate:.3f}；"
            "该任务家族在配对设计下无法检出这个效应"
        )
    center = _approximate_pairs(mismatch_rate, effect, alpha=alpha, target_power=target_power)
    lower = max(2, center - 40)
    upper = min(MAX_PAIRS_SEARCH, center + 400)
    for pairs in range(lower, upper + 1):
        power = power_for(pairs, mismatch_rate, effect, alpha=alpha)
        if power >= target_power:
            planned = max(pairs, floor)
            expected = planned * mismatch_rate
            bounds = rejection_bounds(max(1, int(round(expected))), alpha=alpha)
            return PowerResult(
                pairs=planned,
                mismatch_rate=mismatch_rate,
                effect=effect,
                alpha=alpha,
                power=power_for(planned, mismatch_rate, effect, alpha=alpha),
                expected_discordant=round(expected, 2),
                reject_lower=bounds[0] if bounds else -1,
                reject_upper=bounds[1] if bounds else -1,
            )
    raise PowerAnalysisError(
        f"在 {upper} 对内未达到 {target_power:.0%} 检出力；请重新选择任务家族或最小有意义效应"
    )


def detectable_effect(
    pairs: int,
    mismatch_rate: float,
    *,
    alpha: float = DEFAULT_ALPHA,
    target_power: float = DEFAULT_POWER,
) -> float | None:
    """给定配对数与失配率，找出仍能达到目标检出力的**最小**绝对效应。

    样本量受限时用它如实报告"本样本量在 80% 检出力下只能检出至少 X 个百分点"。

    即使把效应取到上限（`effect == mismatch_rate`，即全部不一致对都同向）也达不到
    目标检出力时返回 `None`：这种情况下**没有任何**效应是"可检出"的，绝不能拿
    失配率上限冒充可检出效应。
    """

    if pairs < 1:
        raise PowerAnalysisError("配对数必须为正")
    if not 0.0 < mismatch_rate <= 1.0:
        raise PowerAnalysisError("失配率必须在 (0, 1] 区间")
    step = 0.001
    # 用整数步进避免浮点累加漂移（否则端点 0.10 可能被 `candidate <= mismatch_rate` 跳过）。
    steps = int(math.floor(mismatch_rate / step))
    for index in range(1, steps + 1):
        candidate = index * step
        if power_for(pairs, mismatch_rate, candidate, alpha=alpha) >= target_power:
            return round(candidate, 3)
    # 端点：效应取到上限（全部不一致对同向）。
    if power_for(pairs, mismatch_rate, mismatch_rate, alpha=alpha) >= target_power:
        return round(mismatch_rate, 3)
    return None


def maximum_power(
    pairs: int,
    mismatch_rate: float,
    *,
    alpha: float = DEFAULT_ALPHA,
) -> float:
    """给定配对数与失配率时能达到的**最高**检出力（效应取到上限）。"""

    return power_for(pairs, mismatch_rate, mismatch_rate, alpha=alpha)


def power_curve(
    mismatch_rate: float,
    *,
    effects: tuple[float, ...] = (0.10, 0.15, 0.20, 0.30),
    pair_counts: tuple[int, ...] = (30, 50, 100, 150, 200, 300),
    alpha: float = DEFAULT_ALPHA,
) -> list[dict[str, float]]:
    """功效曲线：每个效应在每个配对数上的检出力（供报告直接引用）。"""

    rows: list[dict[str, float]] = []
    for effect in effects:
        if effect > mismatch_rate:
            continue
        for pairs in pair_counts:
            rows.append(
                {
                    "pairs": pairs,
                    "effect": effect,
                    "power": round(power_for(pairs, mismatch_rate, effect, alpha=alpha), 4),
                }
            )
    return rows


def plan_for_family(
    mismatch_rate: float,
    *,
    candidates: int = 1,
    effect: float = DEFAULT_MIN_EFFECT,
    alpha: float = DEFAULT_ALPHA,
    target_power: float = DEFAULT_POWER,
) -> dict[str, object]:
    """把"每个家族至少 30 对"与 `N × K` 库存一次算清。"""

    if candidates < 1:
        raise PowerAnalysisError("候选轮数 K 至少为 1")
    result = required_pairs(mismatch_rate, effect, alpha=alpha, target_power=target_power)
    # 计划要求该家族至少准备 N × K 对互不复用的 holdout；K 为计划候选轮数。
    required_holdout = result.pairs * candidates
    return {
        "mismatch_rate": mismatch_rate,
        "effect": effect,
        "alpha": alpha,
        "target_power": target_power,
        "calculated_pairs": result.pairs,
        "floor_pairs": MIN_PAIRS_REQUIRED,
        "planned_pairs": result.pairs,
        "candidates_k": candidates,
        "required_holdout_pairs": required_holdout,
        "expected_discordant": result.expected_discordant,
        "achieved_power": round(result.power, 4),
        "detectable_effect_at_planned_pairs": detectable_effect(
            result.pairs, mismatch_rate, alpha=alpha, target_power=target_power
        ),
        "max_power_at_planned_pairs": round(
            maximum_power(result.pairs, mismatch_rate, alpha=alpha), 4
        ),
        "note": ("配对数取 30 与计算值中的较大者；30 对只防止极小样本，不保证检出 10 个百分点。"),
    }
