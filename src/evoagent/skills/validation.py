"""Skill DSL 的跨字段、依赖图和安全检查。"""

import re
from dataclasses import dataclass
from pathlib import PurePath
from typing import Any

from evoagent.core.models import ToolRisk
from evoagent.skills.schema import InputType, ModelStep, SkillDefinition, ToolStep
from evoagent.tools.registry import ToolRegistry

_REFERENCE = re.compile(r"\$\{([^{}]+)\}")
_STEP_REFERENCE = re.compile(r"^steps\.([a-z][a-z0-9_]{0,63})\.output(?:\.[a-zA-Z0-9_]+)*$")
_INPUT_REFERENCE = re.compile(r"^inputs\.([a-zA-Z_][a-zA-Z0-9_]*)$")
_WINDOWS_ABSOLUTE = re.compile(r"^[a-zA-Z]:[\\/]")
_EMBEDDED_ABSOLUTE = re.compile(r"(?i)(?:[a-z]:[\\/]|(?:^|\s)/(?:home|tmp|var|users|opt)/)")
_DANGEROUS_INSTRUCTION = re.compile(
    r"(?i)(ignore (all |the )?(previous|system) instructions|"
    r"忽略.{0,12}(之前|系统).{0,8}(指令|提示))"
)


class SkillValidationError(ValueError):
    """Skill 结构合法但领域语义或安全边界不合法。"""


@dataclass(frozen=True, slots=True)
class SkillValidationResult:
    ordered_step_ids: tuple[str, ...]


class SkillDefinitionValidator:
    def __init__(
        self,
        registry: ToolRegistry,
        *,
        allowed_tools: frozenset[str],
        max_steps: int = 20,
        max_risk: ToolRisk = ToolRisk.R1,
        supported_schema_version: int = 1,
    ) -> None:
        self._registry = registry
        self._allowed_tools = allowed_tools
        self._max_steps = max_steps
        self._max_risk = max_risk
        self._schema_version = supported_schema_version

    def validate(self, definition: SkillDefinition) -> SkillValidationResult:
        if definition.schema_version != self._schema_version:
            raise SkillValidationError("unsupported skill schema version")
        if len(definition.steps) > self._max_steps:
            raise SkillValidationError("skill exceeds the configured step limit")
        if definition.preconditions.max_effective_risk.value > self._max_risk.value:
            raise SkillValidationError("skill risk exceeds the configured limit")
        declared_tools = set(definition.preconditions.allowed_tools)
        if len(declared_tools) != len(definition.preconditions.allowed_tools):
            raise SkillValidationError("allowed_tools contains duplicates")
        if not declared_tools <= self._allowed_tools:
            raise SkillValidationError("skill declares a tool outside the system allowlist")
        if "shell" in declared_tools:
            raise SkillValidationError("shell is not allowed in declarative skills")

        steps = {step.id: step for step in definition.steps}
        if len(steps) != len(definition.steps):
            raise SkillValidationError("skill step ids must be unique")
        for step in definition.steps:
            if len(set(step.depends_on)) != len(step.depends_on):
                raise SkillValidationError(f"step {step.id} contains duplicate dependencies")
            if step.id in step.depends_on or not set(step.depends_on) <= steps.keys():
                raise SkillValidationError(f"step {step.id} has an invalid dependency")
            if isinstance(step, ToolStep):
                self._validate_tool_step(step, definition, steps)
            else:
                self._validate_text(step.instruction, step.id, set(step.depends_on), definition)
        return SkillValidationResult(self._topological_order(steps))

    def _validate_tool_step(
        self,
        step: ToolStep,
        definition: SkillDefinition,
        steps: dict[str, ToolStep | ModelStep],
    ) -> None:
        if (
            step.tool not in self._registry
            or step.tool not in definition.preconditions.allowed_tools
        ):
            raise SkillValidationError(f"step {step.id} references an unavailable tool")
        tool = self._registry.get(step.tool)
        if tool.risk.value > definition.preconditions.max_effective_risk.value:
            raise SkillValidationError(f"step {step.id} exceeds the skill risk limit")
        allowed_sources = self._ancestors(step.id, steps)
        for value in step.args.values():
            self._validate_value(value, step, allowed_sources, definition)
        if step.foreach is not None:
            self._validate_text(step.foreach, step.id, allowed_sources, definition)
            match = _REFERENCE.fullmatch(step.foreach)
            if match is None:
                raise SkillValidationError(f"step {step.id} foreach must be one reference")
            input_match = _INPUT_REFERENCE.fullmatch(match.group(1))
            if input_match and definition.inputs[input_match.group(1)].type is not InputType.ARRAY:
                raise SkillValidationError(f"step {step.id} foreach input must be an array")

    def _validate_value(
        self,
        value: Any,
        step: ToolStep,
        allowed_sources: set[str],
        definition: SkillDefinition,
    ) -> None:
        if isinstance(value, str):
            self._validate_text(
                value, step.id, allowed_sources, definition, step.foreach is not None
            )
            if PurePath(value).is_absolute() or _WINDOWS_ABSOLUTE.match(value):
                raise SkillValidationError("absolute paths are not allowed in skills")
        elif isinstance(value, list):
            for item in value:
                self._validate_value(item, step, allowed_sources, definition)
        elif isinstance(value, dict):
            for item in value.values():
                self._validate_value(item, step, allowed_sources, definition)

    def _validate_text(
        self,
        value: str,
        step_id: str,
        allowed_sources: set[str],
        definition: SkillDefinition,
        item_allowed: bool = False,
    ) -> None:
        if _DANGEROUS_INSTRUCTION.search(value):
            raise SkillValidationError(f"step {step_id} contains instruction override language")
        if _EMBEDDED_ABSOLUTE.search(value):
            raise SkillValidationError("absolute paths are not allowed in skills")
        for expression in _REFERENCE.findall(value):
            if expression == "item":
                if not item_allowed:
                    raise SkillValidationError(f"step {step_id} uses item outside foreach")
                continue
            input_match = _INPUT_REFERENCE.fullmatch(expression)
            if input_match:
                if input_match.group(1) not in definition.inputs:
                    raise SkillValidationError(f"step {step_id} references an unknown input")
                continue
            step_match = _STEP_REFERENCE.fullmatch(expression)
            if step_match and step_match.group(1) in allowed_sources:
                continue
            raise SkillValidationError(f"step {step_id} contains an invalid reference")
        if "${" in _REFERENCE.sub("", value):
            raise SkillValidationError(f"step {step_id} contains malformed template syntax")

    @staticmethod
    def _ancestors(step_id: str, steps: dict[str, ToolStep | ModelStep]) -> set[str]:
        found: set[str] = set()
        pending = list(steps[step_id].depends_on)
        while pending:
            dependency = pending.pop()
            if dependency not in found:
                found.add(dependency)
                pending.extend(steps[dependency].depends_on)
        return found

    @staticmethod
    def _topological_order(steps: dict[str, ToolStep | ModelStep]) -> tuple[str, ...]:
        remaining = {name: set(step.depends_on) for name, step in steps.items()}
        ordered: list[str] = []
        while remaining:
            ready = sorted(name for name, dependencies in remaining.items() if not dependencies)
            if not ready:
                raise SkillValidationError("skill dependency graph contains a cycle")
            ordered.extend(ready)
            for name in ready:
                remaining.pop(name)
            for dependencies in remaining.values():
                dependencies.difference_update(ready)
        return tuple(ordered)
