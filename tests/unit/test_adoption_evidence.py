"""Correct business answers alone do not prove applicability and adoption."""

from copy import deepcopy
from uuid import uuid4

import pytest

from evoagent.learning.selection_evidence import adoption_contract_passed


@pytest.mark.parametrize("mutation", ["unknown", "positive_missing", "counter_applied", "other"])
def test_adoption_cannot_be_forged_by_passing_business_verdict(mutation):
    candidate = uuid4()
    items = [
        {
            "arm": "treatment",
            "case_kind": "positive",
            "verdict": "pass",
            "actual_selection": {
                "verified": True,
                "applied": True,
                "selection": {"version_id": str(candidate)},
            },
        },
        {
            "arm": "treatment",
            "case_kind": "counterexample",
            "verdict": "pass",
            "actual_selection": {"verified": True, "applied": False, "selection": None},
        },
    ]
    assert adoption_contract_passed(items, candidate)
    changed = deepcopy(items)
    if mutation == "unknown":
        changed[0]["actual_selection"]["verified"] = False
    elif mutation == "positive_missing":
        changed[0]["actual_selection"]["applied"] = False
    elif mutation == "counter_applied":
        changed[1]["actual_selection"]["applied"] = True
    else:
        changed[0]["actual_selection"]["selection"]["version_id"] = str(uuid4())
    assert not adoption_contract_passed(changed, candidate)
