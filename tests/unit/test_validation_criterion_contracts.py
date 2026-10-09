"""Reject vacuous and misleading validators before a dataset is frozen."""

import pytest

from evoagent.evals.validators import default_validator_registry
from evoagent.learning.schema import LearningError
from evoagent.learning.validation import PersonalValidationService
from evoagent.learning.validation_schema import ValidationCriterion


def criterion(name, parameters, *, business=False):
    return ValidationCriterion(
        criterion_id="acceptance",
        kind="machine",
        description="Frozen check",
        expected=True,
        validator={"name": name, "parameters": parameters},
        business_criterion=business,
    )


@pytest.mark.parametrize(
    "name,parameters",
    [
        ("covers_items", {"items": []}),
        ("preserves_constraints", {"required": []}),
        ("tool_policy", {}),
        ("covers_items", {"items": [""]}),
        ("covers_items", {"items": ["001"], "ignored": True}),
        ("expected_status", {"status": {"claimed": "completed"}}),
        ("max_tool_calls", {"maximum": True}),
    ],
)
def test_vacuous_or_malformed_rules_are_refused(name, parameters):
    service = PersonalValidationService(None, None, default_validator_registry())
    with pytest.raises(LearningError):
        service._require_meaningful_spec(criterion(name, parameters))


@pytest.mark.parametrize(
    "name,parameters",
    [
        ("artifact_exists", {"type": "text/csv"}),
        ("minimum_citations", {"minimum": 2}),
        ("contains_sections", {"sections": ["Findings"]}),
    ],
)
def test_structure_cannot_claim_business_verification(name, parameters):
    service = PersonalValidationService(None, None, default_validator_registry())
    with pytest.raises(LearningError, match="structure_is_not_business"):
        service._require_meaningful_spec(criterion(name, parameters, business=True))


def test_explicit_constraints_and_zero_tool_counterexample_are_supported():
    service = PersonalValidationService(None, None, default_validator_registry())
    service._require_meaningful_spec(
        criterion("preserves_constraints", {"required": ["001"], "forbidden": ["changed id"]})
    )
    service._require_meaningful_spec(criterion("max_tool_calls", {"maximum": 0}))
