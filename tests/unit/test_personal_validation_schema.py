import pytest
from pydantic import ValidationError

from evoagent.evals.schema import ValidatorSpec
from evoagent.learning.validation_schema import (
    PersonalValidationCase,
    ValidationCriterion,
    ValidationSubmission,
)


def case(key, kind, data):
    return PersonalValidationCase(
        case_key=key,
        case_kind=kind,
        task_family="data",
        public_input={"goal": "Preserve identifiers as text", "inputs": {"rows": data}},
        criteria=(
            ValidationCriterion(
                criterion_id="identifier_preservation",
                kind="user",
                expected=data,
                description="Identifiers retain their original leading zeros",
                business_criterion=True,
            ),
        ),
    )


def test_freezes_concrete_positive_negative_and_explicit_business_criteria():
    request = ValidationSubmission(
        client_request_id="validation",
        cases=(
            case("positive", "positive", ["001"]),
            case("negative", "counterexample", ["1", "01"]),
        ),
    )
    assert len(request.cases) == 2
    with pytest.raises(ValidationError):
        ValidationSubmission.model_validate({**request.model_dump(), "verdict": "pass"})


def test_completion_cannot_be_declared_business_success():
    with pytest.raises(ValidationError, match="not business verification"):
        ValidationCriterion(
            criterion_id="completion",
            kind="machine",
            expected=True,
            description="task completed",
            validator=ValidatorSpec(name="run_completed"),
            business_criterion=True,
        )


def test_research_citation_or_structure_checks_cannot_replace_user_fact_verification():
    criterion = ValidationCriterion(
        criterion_id="report_structure",
        kind="machine",
        expected=True,
        description="report has sections",
        validator=ValidatorSpec(name="contains_sections", parameters={"sections": ["References"]}),
        business_criterion=True,
    )
    cases = tuple(
        PersonalValidationCase(
            case_key=key,
            case_kind=kind,
            task_family="research",
            public_input={"goal": "Compare evidence", "inputs": {"topic": topic}},
            criteria=(criterion,),
        )
        for key, kind, topic in (
            ("positive", "positive", "first topic"),
            ("negative", "counterexample", "second topic"),
        )
    )
    with pytest.raises(ValidationError, match="user verification"):
        ValidationSubmission(client_request_id="research", cases=cases)


def test_title_change_does_not_make_a_new_input_or_supply_business_success():
    positive = case("positive", "positive", ["001"])
    negative = case("negative", "counterexample", ["001"])
    negative = negative.model_copy(
        update={"public_input": {**negative.public_input, "goal": "another title"}}
    )
    with pytest.raises(ValidationError, match="distinct concrete"):
        ValidationSubmission(client_request_id="same-input", cases=(positive, negative))
    with pytest.raises(ValidationError, match="positive and counterexample"):
        ValidationSubmission(
            client_request_id="missing-negative",
            cases=(positive, case("second", "positive", ["002"])),
        )


def test_machine_origin_requires_registered_validator_not_client_claimed_result():
    with pytest.raises(ValidationError, match="registered validator"):
        ValidationCriterion(
            criterion_id="result", kind="machine", description="claimed success", expected=True
        )
    with pytest.raises(ValidationError):
        ValidationCriterion(
            criterion_id="result",
            kind="user",
            description="human check",
            expected=True,
            verdict="pass",
        )


def test_goal_alone_and_sensitive_inputs_are_not_validation_material():
    criterion = ValidationCriterion(
        criterion_id="business", kind="user", description="check", expected=True
    )
    with pytest.raises(ValidationError, match="concrete structured"):
        PersonalValidationCase(
            case_key="goal_only",
            case_kind="positive",
            task_family="general",
            public_input={"goal": "new title"},
            criteria=(criterion,),
        )

    with pytest.raises(ValidationError, match="safe bound"):
        PersonalValidationCase(
            case_key="secret_input",
            case_kind="positive",
            task_family="data",
            public_input={
                "goal": "check input",
                "inputs": {"dsn": "postgresql://demo:fictional-password@localhost/demo"},
            },
            criteria=(criterion,),
        )


@pytest.mark.parametrize("field", ["public_input", "criterion_expected"])
def test_nested_json_sensitive_keys_are_checked_before_serialization(field):
    original = case("positive", "positive", ["001"]).model_dump(mode="json")
    sensitive = {"rows": [{"API_KEY": "noncredential-test-marker"}]}
    if field == "public_input":
        original["public_input"]["inputs"] = sensitive
    else:
        original["criteria"][0]["expected"] = sensitive
    with pytest.raises(ValidationError, match="safe bound"):
        PersonalValidationCase.model_validate(original)
