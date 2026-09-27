"""M0c 数值预算闸门的测试（实施计划 §5.8）。

覆盖计划的原话：**未填数值不得启动正式评测**；接近上限停止新任务；试跑与正式分账；
价格假设缺失则无法计算费用。
"""

from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import select

from evoagent.config import Settings
from evoagent.db.base import Base
from evoagent.db.models import SpendRecord
from evoagent.db.session import Database
from evoagent.runtime.budget import (
    MICROS_PER_UNIT,
    BudgetExceededError,
    BudgetScope,
    cost_micros,
    evaluate_budget,
    limits_from_settings,
    record_spend,
    spend_totals,
    threshold_micros,
)


async def make_database(tmp_path: Path) -> Database:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'budget.db'}")
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return database


def priced_settings(**overrides) -> Settings:
    base = {
        "budget_input_price_micros_per_million": 2_000_000,  # 2 元/百万输入 token
        "budget_output_price_micros_per_million": 8_000_000,  # 8 元/百万输出 token
    }
    return Settings(**{**base, **overrides})


def test_cost_is_rounded_up_and_rejects_missing_prices(tmp_path: Path) -> None:
    limits = limits_from_settings(priced_settings(), BudgetScope.TRIAL)

    # 1_000_000 输入 token 恰好 2 元。
    assert cost_micros(limits, input_tokens=1_000_000, output_tokens=0) == 2 * MICROS_PER_UNIT
    # 1 个输入 token 也会向上取整成 1 微元以上，而不是 0。
    assert cost_micros(limits, input_tokens=1, output_tokens=0) >= 1

    unpriced = limits_from_settings(Settings(), BudgetScope.TRIAL)
    with pytest.raises(BudgetExceededError, match="价格假设未填写"):
        cost_micros(unpriced, input_tokens=10, output_tokens=10)


@pytest.mark.asyncio
async def test_unfilled_budget_refuses_paid_calls(tmp_path: Path) -> None:
    """默认配置（额度为空）必须拒绝，而不是无限额放行。"""

    database = await make_database(tmp_path)
    try:
        async with database.session_factory() as session:
            status = await evaluate_budget(session, Settings(), scope=BudgetScope.FORMAL)
        assert status.allowed is False
        assert "未填写" in status.reason
    finally:
        await database.dispose()


@pytest.mark.asyncio
async def test_missing_price_assumption_also_refuses(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    try:
        settings = Settings(budget_total_limit_micros=100 * MICROS_PER_UNIT)
        async with database.session_factory() as session:
            status = await evaluate_budget(session, settings, scope=BudgetScope.FORMAL)
        assert status.allowed is False
        assert "价格假设" in status.reason
    finally:
        await database.dispose()


@pytest.mark.asyncio
async def test_stop_threshold_blocks_new_calls(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    try:
        settings = priced_settings(
            budget_trial_limit_micros=10 * MICROS_PER_UNIT,
            budget_stop_ratio=0.5,
        )
        async with database.session_factory() as session:
            first = await evaluate_budget(session, settings, scope=BudgetScope.TRIAL)
        assert first.allowed is True
        assert first.stop_threshold_micros == 5 * MICROS_PER_UNIT

        await record_spend(
            database.session_factory,
            scope=BudgetScope.TRIAL,
            task_id=None,
            run_id=None,
            provider="openai_compatible",
            model="example",
            input_tokens=3_000_000,  # 3 元：超过 50% 停止阈值（5 元）
            output_tokens=0,
            limits=limits_from_settings(settings, BudgetScope.TRIAL),
        )
        async with database.session_factory() as session:
            second = await evaluate_budget(session, settings, scope=BudgetScope.TRIAL)
        assert second.allowed is False
        assert "停止阈值" in second.reason
    finally:
        await database.dispose()


@pytest.mark.asyncio
async def test_trial_and_formal_are_accounted_separately(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    try:
        settings = priced_settings(
            budget_trial_limit_micros=5 * MICROS_PER_UNIT,
            budget_total_limit_micros=100 * MICROS_PER_UNIT,
        )
        await record_spend(
            database.session_factory,
            scope=BudgetScope.TRIAL,
            task_id=None,
            run_id=None,
            provider="openai_compatible",
            model="example",
            input_tokens=1_000_000,
            output_tokens=0,
            limits=limits_from_settings(settings, BudgetScope.TRIAL),
        )

        async with database.session_factory() as session:
            trial_spent, _ = await spend_totals(session, scope=BudgetScope.TRIAL)
            formal_spent, _ = await spend_totals(session, scope=BudgetScope.FORMAL)
            trial = await evaluate_budget(session, settings, scope=BudgetScope.TRIAL)
            formal = await evaluate_budget(session, settings, scope=BudgetScope.FORMAL)

        assert trial_spent == 2 * MICROS_PER_UNIT
        # 试跑花掉的钱不会挤占正式额度。
        assert formal_spent == 0
        assert trial.allowed is True
        assert formal.allowed is True
    finally:
        await database.dispose()


@pytest.mark.asyncio
async def test_task_limit_and_zero_limit_close_the_paid_path(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    try:
        task_id = uuid4()
        settings = priced_settings(
            budget_total_limit_micros=100 * MICROS_PER_UNIT,
            budget_task_limit_micros=1 * MICROS_PER_UNIT,
        )
        await record_spend(
            database.session_factory,
            scope=BudgetScope.FORMAL,
            task_id=task_id,
            run_id=None,
            provider="openai_compatible",
            model="example",
            input_tokens=1_000_000,
            output_tokens=0,
            limits=limits_from_settings(settings, BudgetScope.FORMAL),
        )
        async with database.session_factory() as session:
            blocked = await evaluate_budget(
                session, settings, scope=BudgetScope.FORMAL, task_id=task_id
            )
            other = await evaluate_budget(
                session, settings, scope=BudgetScope.FORMAL, task_id=uuid4()
            )
        assert blocked.allowed is False
        assert "每 Task 限额" in blocked.reason
        assert other.allowed is True

        closed = priced_settings(
            budget_total_limit_micros=100 * MICROS_PER_UNIT, budget_task_limit_micros=0
        )
        async with database.session_factory() as session:
            status = await evaluate_budget(session, closed, scope=BudgetScope.FORMAL)
        assert status.allowed is False
        assert "关闭" in status.reason
    finally:
        await database.dispose()


@pytest.mark.asyncio
async def test_milestone_limit_uses_the_stricter_bound(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    try:
        settings = priced_settings(
            budget_total_limit_micros=100 * MICROS_PER_UNIT,
            budget_milestone_limits_micros={"formal": 3 * MICROS_PER_UNIT},
        )
        limits = limits_from_settings(settings, BudgetScope.FORMAL)
        assert limits.limit_micros == 3 * MICROS_PER_UNIT
        assert threshold_micros(limits) == 3 * MICROS_PER_UNIT
        async with database.session_factory() as session:
            assert (await evaluate_budget(session, settings, scope=BudgetScope.FORMAL)).allowed
    finally:
        await database.dispose()


@pytest.mark.asyncio
async def test_spend_records_are_append_only(tmp_path: Path) -> None:
    database = await make_database(tmp_path)
    try:
        settings = priced_settings(budget_total_limit_micros=100 * MICROS_PER_UNIT)
        await record_spend(
            database.session_factory,
            scope=BudgetScope.FORMAL,
            task_id=None,
            run_id=None,
            provider="openai_compatible",
            model="example",
            input_tokens=1,
            output_tokens=1,
            limits=limits_from_settings(settings, BudgetScope.FORMAL),
        )
        async with database.session_factory() as session:
            record = await session.scalar(select(SpendRecord))
            assert record is not None
            assert record.input_price_micros_per_million == 2_000_000
        # 写入后不可改写：账本是报告的唯一费用依据。
        with pytest.raises(ValueError, match="append-only"):
            async with database.session_factory() as session:
                row = await session.get(SpendRecord, record.id)
                row.cost_micros = 0
                await session.commit()
    finally:
        await database.dispose()
