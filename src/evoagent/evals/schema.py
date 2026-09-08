"""评测数据集、Case 与私有验证器的严格输入契约。"""

from typing import Any

from pydantic import Field, model_validator

from evoagent.core.models import ContractModel
from evoagent.evals.lifecycle import EvalSplit


class ValidatorSpec(ContractModel):
    name: str = Field(pattern=r"^[a-z][a-z0-9_]{1,63}$")
    version: str = Field(default="1", min_length=1, max_length=32)
    parameters: dict[str, Any] = Field(default_factory=dict)


class EvalCaseDefinition(ContractModel):
    case_key: str = Field(pattern=r"^[a-z][a-z0-9_-]{1,127}$")
    task_family: str = Field(min_length=1, max_length=128)
    split: EvalSplit
    public_input: dict[str, Any]
    private_validators: tuple[ValidatorSpec, ...] = Field(min_length=1)
    risk_profile: dict[str, Any] = Field(default_factory=dict)


class EvalDatasetDefinition(ContractModel):
    name: str = Field(pattern=r"^[a-z][a-z0-9_-]{1,127}$")
    version: int = Field(ge=1)
    cases: tuple[EvalCaseDefinition, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def case_keys_are_unique(self) -> "EvalDatasetDefinition":
        keys = [case.case_key for case in self.cases]
        if len(keys) != len(set(keys)):
            raise ValueError("case_key must be unique within a dataset")
        return self
