"""Host-registered real validation identity, without credentials or remote probes."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from evoagent.learning.schema import LearningError
from evoagent.privacy.redaction import detect_sensitive
from evoagent.skills.canonical import canonical_json, content_hash


class ValidationExecutionProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    profile_id: Literal["host-real-v1"] = "host-real-v1"
    schema_version: Literal[1] = 1
    provider: Literal["openai_compatible"]
    model: str = Field(min_length=1, max_length=256)
    endpoint_identity_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    max_output_tokens: int = Field(ge=1, le=32768, strict=True)
    max_iterations: int = Field(ge=1, le=100, strict=True)
    max_total_tokens: int = Field(ge=1, strict=True)
    model_timeout_seconds: float = Field(gt=0)
    task_timeout_seconds: float = Field(gt=0)
    budget_scope: Literal["trial", "formal"]
    global_limit_micros: int = Field(ge=1, strict=True)
    task_limit_micros: int = Field(ge=1, strict=True)
    input_price_micros_per_million: int = Field(ge=0, strict=True)
    output_price_micros_per_million: int = Field(ge=0, strict=True)
    budget_stop_ratio: float = Field(gt=0, le=1)

    def identity(self):
        body = self.model_dump(mode="json")
        return {"profile": body, "profile_hash": content_hash(body)}


def real_profile(settings):
    """Only trusted deployment settings register a paid profile, never a request."""
    if not getattr(settings, "personal_validation_real_enabled", False):
        raise LearningError("personal_real_validation_disabled")
    if (
        settings.provider.value != "openai_compatible"
        or not settings.model
        or not settings.base_url
    ):
        raise LearningError("validation_model_not_registered")
    if settings.api_key is None or not settings.api_key.get_secret_value():
        raise LearningError("validation_model_not_registered")
    limit = (
        settings.budget_trial_limit_micros
        if settings.budget_scope == "trial"
        else settings.budget_total_limit_micros
    )
    values = {
        "provider": settings.provider.value,
        "model": settings.model,
        "endpoint_identity_hash": content_hash(str(settings.base_url)),
        "max_output_tokens": min(settings.model_request_max_output_tokens or 4096, 32768),
        "max_iterations": settings.max_iterations,
        "max_total_tokens": settings.max_total_tokens,
        "model_timeout_seconds": settings.model_timeout_seconds,
        "task_timeout_seconds": settings.task_timeout_seconds,
        "budget_scope": settings.budget_scope,
        "global_limit_micros": limit,
        "task_limit_micros": settings.budget_task_limit_micros,
        "input_price_micros_per_million": settings.budget_input_price_micros_per_million,
        "output_price_micros_per_million": settings.budget_output_price_micros_per_million,
        "budget_stop_ratio": settings.budget_stop_ratio,
    }
    if detect_sensitive(canonical_json(values)):
        raise LearningError("validation_profile_sensitive")
    try:
        return ValidationExecutionProfile.model_validate(values)
    except ValueError:
        raise LearningError("validation_budget_unapproved") from None


def require_frozen_profile(policy, settings):
    """Old Mock contracts retain their identity; real contracts must match host."""
    frozen = policy.get("execution_profile")
    if frozen is None:
        if policy.get("provider") != "mock" or policy.get("model") != "mock":
            raise LearningError("validation_execution_profile_required")
        return None
    try:
        profile = ValidationExecutionProfile.model_validate(frozen)
    except ValueError:
        raise LearningError("validation_execution_profile_invalid") from None
    if (
        content_hash(profile.model_dump(mode="json")) != policy.get("execution_profile_hash")
        or policy.get("provider") != profile.provider
        or policy.get("model") != profile.model
    ):
        raise LearningError("validation_execution_profile_invalid")
    if settings is None or profile != real_profile(settings):
        raise LearningError("validation_execution_profile_changed")
    return profile


def real_trial_eligible(report, policy):
    """Report collection/judging may grant eligibility only with paid evidence."""
    try:
        profile = ValidationExecutionProfile.model_validate(policy.get("execution_profile"))
    except ValueError:
        return False
    if (
        content_hash(profile.model_dump(mode="json")) != policy.get("execution_profile_hash")
        or policy.get("provider") != profile.provider
        or policy.get("model") != profile.model
    ):
        return False
    cost = report.get("cost", {})
    items = report.get("items", ())
    if (
        not isinstance(cost, dict)
        or not isinstance(items, (list, tuple))
        or any(not isinstance(item, dict) for item in items)
    ):
        return False
    return bool(
        policy.get("execution_profile") is not None
        and report.get("execution_profile_hash") == policy.get("execution_profile_hash")
        and cost.get("provider") == "openai_compatible"
        and cost.get("usage_complete") is True
        and type(cost.get("paid_calls")) is int
        and cost["paid_calls"] > 0
        and report.get("business_verification") == "passed"
        and report.get("adoption_verification") == "passed"
        and 2 <= len(items) <= 100
        and {item.get("case_kind") for item in items} == {"positive", "counterexample"}
        and all(
            isinstance(item, dict)
            and item.get("verdict") == "pass"
            and item.get("judge_origin") in {"machine", "user"}
            and item.get("judge_id")
            and item.get("independent_input") is True
            for item in items
        )
    )
