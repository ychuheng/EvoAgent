from uuid import uuid4

import pytest
from pydantic import ValidationError

from evoagent.learning.judgments import HumanCriterionJudgment, HumanJudgmentSubmission


def claim():
    return {
        "eval_run_id": str(uuid4()),
        "criterion_id": "business_check",
        "verdict": "unknown",
        "observed": None,
        "reason": "output cannot be verified yet",
    }


def test_human_unknown_is_allowed_but_model_authority_and_empty_reason_are_not():
    assert HumanCriterionJudgment.model_validate(claim()).verdict == "unknown"
    for extra in ({"actor": "model"}, {"reason": " "}, {"verdict": "approved_by_model"}):
        with pytest.raises(ValidationError):
            HumanCriterionJudgment.model_validate({**claim(), **extra})


def test_duplicate_run_criterion_and_forged_batch_actor_are_rejected():
    item = claim()
    payload = {
        "client_request_id": "checked-1",
        "expected_lock_version": 3,
        "expected_report_hash": "sha256:" + "a" * 64,
        "judgments": [item],
    }
    assert HumanJudgmentSubmission.model_validate(payload)
    for extra in ({"judgments": [item, item]}, {"actor": "user"}):
        with pytest.raises(ValidationError):
            HumanJudgmentSubmission.model_validate({**payload, **extra})


def test_nested_observation_credentials_are_rejected_as_structured_fields():
    with pytest.raises(ValidationError, match="safe evidence"):
        HumanCriterionJudgment.model_validate(
            {**claim(), "observed": {"result": [{"password": "noncredential-test-marker"}]}}
        )
