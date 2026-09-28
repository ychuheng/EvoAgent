import random

from evoagent.runtime.retry import INFRASTRUCTURE_RETRY_CODES, RetryCategory, RetryPolicy


def policy() -> RetryPolicy:
    return RetryPolicy(
        max_attempts=3,
        base_seconds=2,
        max_seconds=10,
        max_elapsed_seconds=30,
        max_total_tokens=100,
        random_source=random.Random(7),
    )


def test_transient_error_gets_bounded_exponential_backoff() -> None:
    decision = policy().decide(
        "provider_timeout",
        attempt_count=2,
        elapsed_seconds=1,
        total_tokens=10,
    )

    assert decision.retry is True
    assert decision.category is RetryCategory.TRANSIENT
    assert decision.delay_seconds is not None
    assert 2 <= decision.delay_seconds <= 6


def test_permanent_and_uncertain_side_effect_errors_are_not_retried() -> None:
    permanent = policy().decide(
        "provider_protocol_error",
        attempt_count=1,
        elapsed_seconds=0,
        total_tokens=0,
    )
    unsafe = policy().decide(
        "provider_timeout",
        attempt_count=1,
        elapsed_seconds=0,
        total_tokens=0,
        side_effect_uncertain=True,
    )

    assert permanent.retry is False
    assert permanent.category is RetryCategory.PERMANENT
    assert unsafe.retry is False
    assert unsafe.category is RetryCategory.UNSAFE_SIDE_EFFECT


def test_attempt_time_and_token_budgets_stop_retrying() -> None:
    attempts = policy().decide(
        "provider_timeout",
        attempt_count=3,
        elapsed_seconds=0,
        total_tokens=0,
    )
    elapsed = policy().decide(
        "provider_timeout",
        attempt_count=2,
        elapsed_seconds=29,
        total_tokens=0,
    )
    tokens = policy().decide(
        "provider_timeout",
        attempt_count=1,
        elapsed_seconds=0,
        total_tokens=100,
    )

    assert attempts.category is RetryCategory.BUDGET_EXHAUSTED
    assert elapsed.category is RetryCategory.BUDGET_EXHAUSTED
    assert tokens.category is RetryCategory.BUDGET_EXHAUSTED


def test_dependency_unavailable_is_transient_and_never_burns_an_attempt() -> None:
    """配额依赖（Redis）不可用是基础设施问题：按瞬时错误退避重试，且不计入尝试次数。

    否则一次 Redis 抖动就会消耗任务的重试预算，等依赖恢复时任务已经"用满 3 次"而不再重试。
    """

    decision = policy().decide(
        "dependency_unavailable",
        attempt_count=1,
        elapsed_seconds=0,
        total_tokens=0,
    )

    assert decision.retry is True
    assert decision.category is RetryCategory.TRANSIENT
    assert "rate_limited" in INFRASTRUCTURE_RETRY_CODES
    assert "dependency_unavailable" in INFRASTRUCTURE_RETRY_CODES
    # 预算拒绝是策略决定，不是基础设施故障：不该被豁免，也不该重试。
    assert "budget_exceeded" not in INFRASTRUCTURE_RETRY_CODES
    budget = policy().decide(
        "budget_exceeded",
        attempt_count=1,
        elapsed_seconds=0,
        total_tokens=0,
    )
    assert budget.retry is False
    assert budget.category is RetryCategory.PERMANENT
