import random

from evoagent.runtime.retry import RetryCategory, RetryPolicy


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
