import pytest
from pydantic import SecretStr

from evoagent.config import ProviderName, Settings
from evoagent.learning.schema import LearningError
from evoagent.learning.validation_profiles import (
    real_profile,
    real_trial_eligible,
    require_frozen_profile,
)


def host(**changes):
    return Settings(_env_file=None).model_copy(
        update={
            "personal_validation_real_enabled": True,
            "provider": ProviderName.OPENAI_COMPATIBLE,
            "model": "fixture-model",
            "base_url": "https://model.example.test/v1",
            "api_key": SecretStr("fixture-credential"),
            "budget_total_limit_micros": 100000,
            "budget_task_limit_micros": 10000,
            "budget_input_price_micros_per_million": 100,
            "budget_output_price_micros_per_million": 200,
            **changes,
        }
    )


def freeze(settings):
    profile = real_profile(settings)
    identity = profile.identity()
    return {
        "provider": profile.provider,
        "model": profile.model,
        "execution_profile": identity["profile"],
        "execution_profile_hash": identity["profile_hash"],
    }


def test_profile_is_host_selected_and_never_serializes_credentials():
    settings = host()
    policy = freeze(settings)
    assert require_frozen_profile(policy, settings) == real_profile(settings)
    assert "fixture-credential" not in str(policy)
    assert "model.example.test" not in str(policy)
    assert freeze(host(api_key=SecretStr("rotated-fixture-credential"))) == policy


@pytest.mark.parametrize(
    "key,value",
    [
        ("model", "another-model"),
        ("base_url", "https://other.example.test/v1"),
        ("max_iterations", 2),
        ("model_request_max_output_tokens", 128),
        ("budget_input_price_micros_per_million", 101),
        ("budget_total_limit_micros", 200000),
    ],
)
def test_frozen_profile_rejects_host_configuration_drift(key, value):
    with pytest.raises(LearningError, match="validation_execution_profile_changed"):
        require_frozen_profile(freeze(host()), host(**{key: value}))


@pytest.mark.parametrize(
    "change",
    [
        {"budget_total_limit_micros": None},
        {"budget_task_limit_micros": None},
        {"budget_total_limit_micros": 0},
        {"budget_input_price_micros_per_million": None},
    ],
)
def test_no_numeric_approval_means_no_real_profile(change):
    with pytest.raises(LearningError, match="validation_budget_unapproved"):
        real_profile(host(**change))


def test_mock_history_is_unchanged_and_real_without_identity_is_refused():
    policy = {"provider": "mock", "model": "mock", "selection_contract_version": 3}
    assert require_frozen_profile(policy, None) is None
    assert "execution_profile" not in policy
    with pytest.raises(LearningError, match="validation_execution_profile_required"):
        require_frozen_profile({"provider": "openai_compatible", "model": "fixture-model"}, host())
    corrupted = freeze(host())
    corrupted["execution_profile"]["max_iterations"] = 2
    with pytest.raises(LearningError, match="validation_execution_profile_invalid"):
        require_frozen_profile(corrupted, host())


def test_real_eligibility_needs_known_paid_usage_adoption_and_all_judgments():
    from copy import deepcopy

    policy = freeze(host())
    report = {
        "execution_profile_hash": policy["execution_profile_hash"],
        "cost": {"provider": "openai_compatible", "usage_complete": True, "paid_calls": 2},
        "business_verification": "passed",
        "adoption_verification": "passed",
        "items": [
            {
                "case_kind": kind,
                "verdict": "pass",
                "judge_origin": "user",
                "judge_id": "local-user",
                "independent_input": True,
            }
            for kind in ("positive", "counterexample")
        ],
    }
    # Synthetic metadata proves the decision contract, not real-model efficacy.
    assert real_trial_eligible(report, policy)
    for field, value in (("provider", "mock"), ("usage_complete", False), ("paid_calls", 0)):
        changed = deepcopy(report)
        changed["cost"][field] = value
        assert not real_trial_eligible(changed, policy)
    for field, value in (
        ("verdict", "fail"),
        ("verdict", "unknown"),
        ("judge_origin", "model"),
        ("judge_id", None),
        ("independent_input", False),
    ):
        changed = deepcopy(report)
        changed["items"][0][field] = value
        assert not real_trial_eligible(changed, policy)
    assert not real_trial_eligible(report, {"provider": "mock"})
    broken = deepcopy(policy)
    broken["execution_profile"]["max_iterations"] = 2
    assert not real_trial_eligible(report, broken)
    assert not real_trial_eligible(report, {"execution_profile": {}})
    changed = deepcopy(report)
    changed["items"][0] = None
    assert not real_trial_eligible(changed, policy)
