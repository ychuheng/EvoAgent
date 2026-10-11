from copy import deepcopy

import pytest

from evoagent.learning.validation_verdicts import business_verification, comparison_verification


def pairs():
    return [
        dict(
            case_key=kind,
            case_kind=kind,
            repeat=0,
            criterion_id="result",
            arm=arm,
            verdict="pass",
            business_criterion=True,
            comparable=True,
            independent_input=True,
            judge_origin="user",
            judge_id="local-user",
        )
        for kind in ("positive", "counterexample")
        for arm in ("control", "treatment")
    ]


def test_repaired_positive_failure_is_improvement_not_failed_business():
    rows = pairs()
    assert comparison_verification(rows) == "equivalent"
    rows[0]["verdict"] = "fail"
    assert comparison_verification(rows) == "improved"
    assert business_verification(rows) == "passed"
    rows[1]["verdict"] = "fail"
    assert comparison_verification(rows) == "failed"
    assert business_verification(rows) == "failed"


@pytest.mark.parametrize(
    "change",
    [
        {"verdict": "unknown"},
        {"comparable": False},
        {"independent_input": False},
        {"judge_origin": "model"},
        {"judge_id": None},
    ],
)
def test_incomplete_or_untrusted_control_cannot_grant_trial(change):
    rows = pairs()
    rows[0].update(change)
    assert comparison_verification(rows) == "pending"


def test_counterexample_or_nonbusiness_control_failure_is_not_improvement():
    rows = pairs()
    rows[2]["verdict"] = "fail"
    assert comparison_verification(rows) == "failed"
    rows = pairs()
    rows[0]["verdict"] = "fail"
    rows[0]["business_criterion"] = rows[1]["business_criterion"] = False
    assert comparison_verification(rows) == "failed"
    assert comparison_verification(pairs()[:-1]) == "pending"
    assert comparison_verification([*rows, deepcopy(rows[0])]) == "pending"
