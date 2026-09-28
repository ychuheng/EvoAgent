"""M0c 数值预算闸门：额度、价格假设、停止阈值与花费账本。

计划 §5.8 的要求与这里的对应关系：

- **未填数值不得启动正式评测**：额度或价格假设未填时，`BudgetGate.check` 直接拒绝，
  所以默认配置下（全为 `None`）付费路径是关闭的，而不是"无限额放行"。
- **执行器接近上限时停止新任务**：每次付费调用前先查该 scope 的累计花费与单 Task 花费，
  达到 `stop_ratio` 阈值即拒绝，不再开始新调用。
- **试跑与正式分账**：花费按 scope（`trial` / `formal`）分开累计，试跑额度不会挤占正式额度。
- **不得只报告便宜的成功样本**：账本逐次记录 token 与费用，报告可以按 scope 求和核对。

金额一律用**微元整数**（1 元 = 1_000_000 微元）以避免浮点误差；向上取整，宁可高估不低估。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from evoagent.db.models import SpendRecord
from evoagent.providers.base import ProviderError

MICROS_PER_UNIT = 1_000_000


class BudgetScope(StrEnum):
    """付费额度分账：试跑额度与正式额度互不挤占。"""

    TRIAL = "trial"
    FORMAL = "formal"


class BudgetExceededError(ProviderError):
    """额度不足、未填数值或已达到停止阈值。

    必须是 `ProviderError`：预算闸门在**发出请求之前**拒绝，异常从 provider 包装层抛出，
    只有归一化过的 Provider 错误才会被 AgentLoop 如实写成 `model.failed` 的 `error_code`。
    早先它只是普通 `Exception`，于是终态落成通用码 `persistent_runtime_error`，
    运维看到的是"运行时崩了"，而不是"额度用完了/没配额度"。
    """

    def __init__(self, message: str) -> None:
        super().__init__(message, code="budget_exceeded")

    code = "budget_exceeded"


@dataclass(frozen=True, slots=True)
class BudgetLimits:
    """一次闸门判断需要的全部数值。任一必需项缺失即视为未填。"""

    scope: BudgetScope
    limit_micros: int | None
    task_limit_micros: int | None
    stop_ratio: float
    input_price_micros_per_million: int | None
    output_price_micros_per_million: int | None

    @property
    def priced(self) -> bool:
        return (
            self.input_price_micros_per_million is not None
            and self.output_price_micros_per_million is not None
        )


@dataclass(frozen=True, slots=True)
class BudgetStatus:
    scope: BudgetScope
    limit_micros: int | None
    spent_micros: int
    task_spent_micros: int
    task_limit_micros: int | None
    stop_threshold_micros: int | None
    allowed: bool
    reason: str


def limits_from_settings(settings, scope: BudgetScope) -> BudgetLimits:
    """从配置解析某个 scope 的额度；整数与价格假设都按"未填即 None"处理。"""

    if scope is BudgetScope.TRIAL:
        limit = settings.budget_trial_limit_micros
    else:
        limit = settings.budget_total_limit_micros
        milestone = settings.budget_milestone_limits_micros.get(scope.value)
        if milestone is not None:
            # 里程碑额度与总额度取更小者，避免两个上限互相"放宽"。
            limit = milestone if limit is None else min(limit, milestone)
    return BudgetLimits(
        scope=scope,
        limit_micros=limit,
        task_limit_micros=settings.budget_task_limit_micros,
        stop_ratio=settings.budget_stop_ratio,
        input_price_micros_per_million=settings.budget_input_price_micros_per_million,
        output_price_micros_per_million=settings.budget_output_price_micros_per_million,
    )


def cost_micros(limits: BudgetLimits, *, input_tokens: int, output_tokens: int) -> int:
    """按价格假设计算一次调用的费用（微元，向上取整）。"""

    if not limits.priced:
        raise BudgetExceededError("价格假设未填写，无法计算费用；正式评测不得启动")
    assert limits.input_price_micros_per_million is not None
    assert limits.output_price_micros_per_million is not None
    numerator = (
        input_tokens * limits.input_price_micros_per_million
        + output_tokens * limits.output_price_micros_per_million
    )
    # 向上取整：宁可高估，也不因为取整少算。
    return -(-numerator // MICROS_PER_UNIT)


def threshold_micros(limits: BudgetLimits) -> int | None:
    if limits.limit_micros is None:
        return None
    return int(limits.limit_micros * limits.stop_ratio)


async def spend_totals(
    session: AsyncSession, *, scope: BudgetScope, task_id: UUID | None = None
) -> tuple[int, int]:
    """返回 (scope 累计微元, 指定 Task 累计微元)。"""

    scope_total = await session.scalar(
        select(func.coalesce(func.sum(SpendRecord.cost_micros), 0)).where(
            SpendRecord.scope == scope.value
        )
    )
    task_total = 0
    if task_id is not None:
        task_total = await session.scalar(
            select(func.coalesce(func.sum(SpendRecord.cost_micros), 0)).where(
                SpendRecord.task_id == task_id
            )
        )
    return int(scope_total or 0), int(task_total or 0)


async def evaluate_budget(
    session: AsyncSession,
    settings,
    *,
    scope: BudgetScope,
    task_id: UUID | None = None,
) -> BudgetStatus:
    """判断当前是否可以再发起一次付费调用。"""

    limits = limits_from_settings(settings, scope)
    if limits.limit_micros is None:
        return BudgetStatus(
            scope=scope,
            limit_micros=None,
            spent_micros=0,
            task_spent_micros=0,
            task_limit_micros=limits.task_limit_micros,
            stop_threshold_micros=None,
            allowed=False,
            reason=f"{scope.value} 额度上限未填写；按计划未填数值不得启动付费评测",
        )
    if not limits.priced:
        return BudgetStatus(
            scope=scope,
            limit_micros=limits.limit_micros,
            spent_micros=0,
            task_spent_micros=0,
            task_limit_micros=limits.task_limit_micros,
            stop_threshold_micros=threshold_micros(limits),
            allowed=False,
            reason="价格假设未填写，无法计算费用；正式评测不得启动",
        )
    if limits.task_limit_micros is not None and limits.task_limit_micros == 0:
        return BudgetStatus(
            scope=scope,
            limit_micros=limits.limit_micros,
            spent_micros=0,
            task_spent_micros=0,
            task_limit_micros=0,
            stop_threshold_micros=threshold_micros(limits),
            allowed=False,
            reason="每 Task 限额为 0，付费调用被关闭",
        )

    spent, task_spent = await spend_totals(session, scope=scope, task_id=task_id)
    threshold = threshold_micros(limits)
    allowed = True
    reason = "ok"
    if threshold is not None and spent >= threshold:
        allowed = False
        reason = f"已达停止阈值（{spent} >= {threshold} 微元），不再开始新调用"
    elif limits.task_limit_micros is not None and task_spent >= limits.task_limit_micros:
        allowed = False
        reason = f"已达每 Task 限额（{task_spent} >= {limits.task_limit_micros} 微元）"
    return BudgetStatus(
        scope=scope,
        limit_micros=limits.limit_micros,
        spent_micros=spent,
        task_spent_micros=task_spent,
        task_limit_micros=limits.task_limit_micros,
        stop_threshold_micros=threshold,
        allowed=allowed,
        reason=reason,
    )


async def record_spend(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    scope: BudgetScope,
    task_id: UUID | None,
    run_id: UUID | None,
    provider: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    limits: BudgetLimits,
) -> int:
    """记录一次付费调用的用量与费用，返回本次费用（微元）。"""

    cost = cost_micros(limits, input_tokens=input_tokens, output_tokens=output_tokens)
    async with session_factory() as session:
        session.add(
            SpendRecord(
                id=uuid4(),
                scope=scope.value,
                task_id=task_id,
                run_id=run_id,
                provider=provider,
                model=model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_micros=cost,
                input_price_micros_per_million=limits.input_price_micros_per_million,
                output_price_micros_per_million=limits.output_price_micros_per_million,
                created_at=datetime.now(UTC),
            )
        )
        await session.commit()
    return cost


__all__ = [
    "MICROS_PER_UNIT",
    "BudgetExceededError",
    "BudgetLimits",
    "BudgetScope",
    "BudgetStatus",
    "cost_micros",
    "evaluate_budget",
    "limits_from_settings",
    "record_spend",
    "spend_totals",
    "threshold_micros",
]
