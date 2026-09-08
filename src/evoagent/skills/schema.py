"""阶段三声明式 Skill DSL 契约。"""

from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import Field, JsonValue, model_validator

from evoagent.core.models import ContractModel, ToolRisk


class InputType(StrEnum):
    STRING = "string"
    INTEGER = "integer"
    NUMBER = "number"
    BOOLEAN = "boolean"
    ARRAY = "array"


class InputDefinition(ContractModel):
    type: InputType
    item_type: InputType | None = None
    required: bool = True
    description: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def validate_item_type(self) -> Self:
        if self.type is InputType.ARRAY and self.item_type is None:
            raise ValueError("array inputs require item_type")
        if self.type is not InputType.ARRAY and self.item_type is not None:
            raise ValueError("only array inputs may define item_type")
        if self.item_type is InputType.ARRAY:
            raise ValueError("nested array inputs are not supported")
        return self


class SkillPreconditions(ContractModel):
    allowed_tools: tuple[str, ...] = Field(min_length=1, max_length=32)
    max_effective_risk: ToolRisk = ToolRisk.R1


class ToolStep(ContractModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    action: Literal["tool"] = "tool"
    tool: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    depends_on: tuple[str, ...] = ()
    foreach: str | None = Field(default=None, max_length=256)
    args: dict[str, JsonValue] = Field(default_factory=dict)


class ModelStep(ContractModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    action: Literal["model"] = "model"
    depends_on: tuple[str, ...] = ()
    instruction: str = Field(min_length=1, max_length=8_000)
    expected_output: str | None = Field(default=None, max_length=1_000)


SkillStep = Annotated[ToolStep | ModelStep, Field(discriminator="action")]


class SkillDefinition(ContractModel):
    schema_version: int = Field(default=1, ge=1)
    name: str = Field(pattern=r"^[a-z][a-z0-9_]{2,63}$")
    description: str = Field(min_length=1, max_length=2_000)
    triggers: tuple[str, ...] = Field(min_length=1, max_length=32)
    inputs: dict[str, InputDefinition] = Field(default_factory=dict)
    preconditions: SkillPreconditions
    steps: tuple[SkillStep, ...] = Field(min_length=1, max_length=100)
    success_criteria: tuple[str, ...] = Field(min_length=1, max_length=32)
    validators: tuple[str, ...] = Field(min_length=1, max_length=32)
