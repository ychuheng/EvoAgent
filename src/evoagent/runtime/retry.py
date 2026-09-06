"""错误分类、指数退避与重试预算。"""

import random
from dataclasses import dataclass
from enum import StrEnum


class RetryCategory(StrEnum):
    TRANSIENT = "transient"
    RATE_LIMITED = "rate_limited"
    PERMANENT = "permanent"
    REQUIRES_USER = "requires_user"
    UNSAFE_SIDE_EFFECT = "unsafe_side_effect"
    BUDGET_EXHAUSTED = "budget_exhausted"


@dataclass(frozen=True, slots=True)
class RetryDecision:
    retry: bool
    category: RetryCategory
    delay_seconds: float | None
    reason: str


_TRANSIENT_CODES = {
    "provider_timeout",
    "provider_network_error",
    "provider_server_error",
    "provider_connection_error",
    "provider_unavailable",
    "internal_provider_error",
    "tool_timeout",
    "temporary_tool_error",
}
_RATE_LIMIT_CODES = {
    "provider_rate_limit",
    "provider_rate_limited",
    "rate_limit",
    "http_429",
}
_REQUIRES_USER_CODES = {
    "approval_required",
    "authentication_required",
    "provider_auth_error",
}


class RetryPolicy:
    """只允许已知瞬时错误在预算范围内自动重试。"""

    def __init__(
        self,
        *,
        max_attempts: int,
        base_seconds: float,
        max_seconds: float,
        max_elapsed_seconds: float,
        max_total_tokens: int,
        random_source: random.Random | None = None,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        if not 0 < base_seconds <= max_seconds:
            raise ValueError("retry delay bounds are invalid")
        if max_elapsed_seconds <= 0 or max_total_tokens < 1:
            raise ValueError("retry budgets must be positive")
        self._max_attempts = max_attempts
        self._base_seconds = base_seconds
        self._max_seconds = max_seconds
        self._max_elapsed_seconds = max_elapsed_seconds
        self._max_total_tokens = max_total_tokens
        self._random = random_source or random.Random()

    def decide(
        self,
        error_code: str,
        *,
        attempt_count: int,
        elapsed_seconds: float,
        total_tokens: int,
        side_effect_uncertain: bool = False,
        retry_after_seconds: float | None = None,
    ) -> RetryDecision:
        if side_effect_uncertain:
            return RetryDecision(
                retry=False,
                category=RetryCategory.UNSAFE_SIDE_EFFECT,
                delay_seconds=None,
                reason="uncertain side effects require manual review",
            )
        if error_code in _REQUIRES_USER_CODES:
            return RetryDecision(
                retry=False,
                category=RetryCategory.REQUIRES_USER,
                delay_seconds=None,
                reason="the failure requires user input",
            )
        if error_code in _RATE_LIMIT_CODES:
            category = RetryCategory.RATE_LIMITED
        elif error_code in _TRANSIENT_CODES:
            category = RetryCategory.TRANSIENT
        else:
            return RetryDecision(
                retry=False,
                category=RetryCategory.PERMANENT,
                delay_seconds=None,
                reason="the error is not classified as transient",
            )
        if attempt_count >= self._max_attempts:
            return self._budget_exhausted("maximum retry attempts reached")
        if total_tokens >= self._max_total_tokens:
            return self._budget_exhausted("token budget reached")

        delay = self._delay(attempt_count, retry_after_seconds)
        if elapsed_seconds + delay > self._max_elapsed_seconds:
            return self._budget_exhausted("retry elapsed-time budget reached")
        return RetryDecision(
            retry=True,
            category=category,
            delay_seconds=delay,
            reason="transient failure may be retried",
        )

    def _delay(self, attempt_count: int, retry_after_seconds: float | None) -> float:
        if retry_after_seconds is not None:
            if retry_after_seconds < 0:
                raise ValueError("retry_after_seconds cannot be negative")
            return min(retry_after_seconds, self._max_seconds)
        else:
            exponent = max(attempt_count - 1, 0)
            base_delay = min(self._base_seconds * (2**exponent), self._max_seconds)
        return min(base_delay * self._random.uniform(0.5, 1.5), self._max_seconds)

    @staticmethod
    def _budget_exhausted(reason: str) -> RetryDecision:
        return RetryDecision(
            retry=False,
            category=RetryCategory.BUDGET_EXHAUSTED,
            delay_seconds=None,
            reason=reason,
        )
