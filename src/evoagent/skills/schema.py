"""阶段三声明式 Skill DSL 契约。"""

from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import Field, JsonValue, model_serializer, model_validator

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


class ApprovalPoint(ContractModel):
    """审批点（M6 S-02）：这一步在什么条件下必须停下来等人决定。"""

    step_id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    condition: str = Field(min_length=1, max_length=1_000)
    reason: str = Field(min_length=1, max_length=1_000)


class Counterexample(ContractModel):
    """反例（M6 S-02）：什么情况下**不要**用这条 Skill，以及为什么。

    计划要求"Skill 不隐含项目路径或凭据，也不把一次偶然成功泛化为规则"，
    反例就是这条约束的落地方式：把适用边界写成可读的反面例子。
    """

    situation: str = Field(min_length=1, max_length=1_000)
    why_not: str = Field(min_length=1, max_length=1_000)
    origin: Literal["hypothetical", "observed", "unknown"] = "unknown"
    evidence_refs: tuple[str, ...] = Field(default=(), max_length=20)

    @model_validator(mode="after")
    def require_observed_evidence(self) -> Self:
        if self.origin == "observed" and not self.evidence_refs:
            raise ValueError("observed counterexamples require evidence references")
        return self


class SkillFactCondition(ContractModel):
    key: Literal[
        "project.available", "input.exists", "input.file_type", "task.family", "tool.available"
    ]
    op: Literal["eq", "ne", "in", "contains"] = "eq"
    value: str | bool | int | tuple[str, ...]

    @model_validator(mode="after")
    def validate_operation(self) -> Self:
        if isinstance(self.value, str) and len(self.value) > 200:
            raise ValueError("fact values are limited to 200 characters")
        if isinstance(self.value, tuple) and (
            len(self.value) > 32 or any(len(item) > 200 for item in self.value)
        ):
            raise ValueError("fact membership values exceed the finite list bound")
        if self.op == "in" and (not isinstance(self.value, tuple) or not self.value):
            raise ValueError("in requires a nonempty finite list")
        if self.op == "contains" and not isinstance(self.value, str):
            raise ValueError("contains requires a string")
        return self


TaskFamily = Literal["general", "coding", "research", "document", "data", "file_management"]


class SkillApplicability(ContractModel):
    task_families: tuple[TaskFamily, ...] = Field(min_length=1, max_length=6)
    file_types: tuple[str, ...] = Field(default=(), max_length=32)
    required_facts: tuple[SkillFactCondition, ...] = Field(default=(), max_length=32)
    excluded_conditions: tuple[SkillFactCondition, ...] = Field(default=(), max_length=32)

    @model_validator(mode="after")
    def validate_file_types(self) -> Self:
        import re

        if any(re.fullmatch(r"[a-z0-9]{1,12}", value) is None for value in self.file_types):
            raise ValueError("file types must be lowercase extensions without paths")
        return self


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
    # M6 S-02 要求候选写清"停止条件、审批点和反例"；默认空元组保持对既有候选的兼容，
    # 但 M6 的候选由 `skills.extraction` 检查为非空（见 `require_s6_annotations`）。
    stop_conditions: tuple[str, ...] = Field(default=(), max_length=32)
    approval_points: tuple[ApprovalPoint, ...] = Field(default=(), max_length=32)
    counterexamples: tuple[Counterexample, ...] = Field(default=(), max_length=32)
    success_criteria: tuple[str, ...] = Field(min_length=1, max_length=32)
    validators: tuple[str, ...] = Field(min_length=1, max_length=32)
    applicability: SkillApplicability | None = None
    rationale: str | None = Field(default=None, min_length=1, max_length=1000)

    @model_serializer(mode="wrap")
    def versioned_body(self, handler):
        body = handler(self)
        if self.schema_version == 1:
            body.pop("applicability", None)
            body.pop("rationale", None)
            for counterexample in body.get("counterexamples", ()):
                counterexample.pop("origin", None)
                counterexample.pop("evidence_refs", None)
        return body

    @model_validator(mode="after")
    def approval_points_reference_existing_steps(self) -> Self:
        if self.schema_version == 1 and (
            self.applicability is not None
            or self.rationale is not None
            or any(item.origin != "unknown" or item.evidence_refs for item in self.counterexamples)
        ):
            raise ValueError("schema v1 cannot silently carry v2 applicability")
        if self.schema_version == 2:
            if self.applicability is None or self.rationale is None:
                raise ValueError("schema v2 requires applicability and rationale")
            if not self.stop_conditions or not self.counterexamples:
                raise ValueError("schema v2 requires stop conditions and counterexamples")
            if any(item.origin == "unknown" for item in self.counterexamples):
                raise ValueError("schema v2 counterexamples require an evidence origin")
        known = {step.id for step in self.steps}
        unknown = [item.step_id for item in self.approval_points if item.step_id not in known]
        if unknown:
            raise ValueError(f"approval_points reference unknown steps: {sorted(unknown)}")
        duplicates = [item for item in self.stop_conditions if self.stop_conditions.count(item) > 1]
        if duplicates:
            raise ValueError("stop_conditions must be unique")
        return self
