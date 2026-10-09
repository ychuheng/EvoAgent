"""User-frozen validation cases and criteria; clients cannot supply verdicts."""

import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from evoagent.evals.schema import ValidatorSpec
from evoagent.privacy.redaction import detect_sensitive


class ValidationCriterion(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    criterion_id: str = Field(pattern=r"^[a-z][a-z0-9_]{1,63}$")
    kind: Literal["machine", "user"]
    description: str = Field(min_length=1, max_length=1000)
    expected: JsonValue
    validator: ValidatorSpec | None = None
    business_criterion: bool = False

    @model_validator(mode="after")
    def require_authoritative_judge(self):
        if (self.kind == "machine") != (self.validator is not None):
            raise ValueError("machine criterion requires a registered validator")
        body = json.dumps(self.model_dump(mode="json"), ensure_ascii=False, sort_keys=True)
        if len(body.encode()) > 8192 or detect_sensitive(body):
            raise ValueError("validation criterion exceeds the safe bound")
        return self


class PersonalValidationCase(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    case_key: str = Field(pattern=r"^[a-z][a-z0-9_-]{1,63}$")
    case_kind: Literal["positive", "counterexample"]
    task_family: Literal["general", "coding", "research", "document", "data", "file_management"]
    public_input: dict[str, JsonValue]
    fixture_id: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_-]{1,63}$")
    criteria: tuple[ValidationCriterion, ...] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def check_safe_frozen_input(self):
        identifiers = [item.criterion_id for item in self.criteria]
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("criterion ids must be unique within each case")
        body = json.dumps(self.public_input, ensure_ascii=False, sort_keys=True)
        if len(body.encode()) > 32768 or detect_sensitive(body):
            raise ValueError("validation input exceeds the safe bound")
        goal = self.public_input.get("goal")
        if not isinstance(goal, str) or not 1 <= len(goal) <= 8000:
            raise ValueError("validation case requires an explicit bounded goal")
        concrete = self.public_input.get("inputs")
        if self.fixture_id is None and not isinstance(concrete, (dict, list)):
            raise ValueError("concrete structured inputs or a registered fixture are required")
        if self.fixture_id is None and not concrete:
            raise ValueError("concrete inputs cannot be empty")
        return self


class ValidationSubmission(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    client_request_id: str = Field(min_length=1, max_length=128)
    cases: tuple[PersonalValidationCase, ...] = Field(min_length=2, max_length=10)
    repeats: int = Field(default=1, ge=1, le=3)

    @model_validator(mode="after")
    def require_positive_and_negative(self):
        keys = [item.case_key for item in self.cases]
        if len(set(keys)) != len(keys):
            raise ValueError("validation case keys must be unique")
        if {item.case_kind for item in self.cases} != {"positive", "counterexample"}:
            raise ValueError("personal validation needs positive and counterexample cases")
        if not any(item.business_criterion for case in self.cases for item in case.criteria):
            raise ValueError("explicit business acceptance criterion is required")
        # A title-only change cannot turn one input into two independent cases.
        fingerprints = [
            json.dumps(
                {"inputs": case.public_input.get("inputs"), "fixture_id": case.fixture_id},
                sort_keys=True,
                separators=(",", ":"),
            )
            for case in self.cases
        ]
        if len(set(fingerprints)) != len(fingerprints):
            raise ValueError("validation cases must have distinct concrete inputs")
        return self
