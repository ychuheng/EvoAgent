"""Finite, side-effect-free applicability checks over frozen host facts."""

from dataclasses import dataclass
from pathlib import PurePosixPath

from evoagent.memory.schema import MemoryError
from evoagent.projects.inputs import InputSet
from evoagent.skills.schema import SkillApplicability, SkillFactCondition
from evoagent.tools.builtin.project_file_read import ProjectFileReadTool


@dataclass(frozen=True)
class ApplicabilityDecision:
    status: str
    reasons: tuple[str, ...] = ()
    missing_facts: tuple[str, ...] = ()


def compare(condition: SkillFactCondition, facts) -> bool | None:
    actual = facts.get(condition.key)
    if actual is None:
        return None
    expected = condition.value
    if condition.op in {"eq", "ne"}:
        if type(actual) is not type(expected):
            return None  # e.g. True is not verified integer 1
        equal = actual == expected
        return equal if condition.op == "eq" else not equal
    if condition.op == "in":
        return actual in expected if isinstance(actual, str) else None
    if condition.op == "contains":
        if isinstance(actual, (tuple, list, frozenset)) and all(
            isinstance(value, str) for value in actual
        ):
            return expected in actual
        # "contains" tests membership of an explicitly enumerated fact, not
        # substring matching of tool names or arbitrary natural-language text.
        return None
    return None


def assess(applicability: SkillApplicability, facts) -> ApplicabilityDecision:
    missing, reasons = set(), []
    family = facts.get("task.family")
    if family is None:
        missing.add("task.family")
    elif family not in applicability.task_families:
        reasons.append("task_family_mismatch")
    if applicability.file_types:
        file_type = facts.get("input.file_type")
        if file_type is None:
            missing.add("input.file_type")
        elif file_type not in applicability.file_types:
            reasons.append("file_type_mismatch")
    for condition in applicability.required_facts:
        result = compare(condition, facts)
        if result is None:
            missing.add(condition.key)
        elif not result:
            reasons.append("required_fact_failed:" + condition.key)
    for condition in applicability.excluded_conditions:
        result = compare(condition, facts)
        if result is None:
            missing.add(condition.key)
        elif result:
            reasons.append("excluded_condition:" + condition.key)
    if reasons:
        return ApplicabilityDecision("inapplicable", tuple(reasons), tuple(sorted(missing)))
    if missing:
        return ApplicabilityDecision("unknown", (), tuple(sorted(missing)))
    return ApplicabilityDecision("applicable")


@dataclass(frozen=True, slots=True)
class VerifiedSkillFacts:
    tools: tuple[str, ...]
    project_available: bool | None = None
    input_exists: bool | None = None
    input_file_type: str | None = None
    task_family: str | None = None

    @classmethod
    def from_runtime(cls, registry, frozen_inputs=None, *, task_family=None):
        # Actual host tool implementation, not a model-provided tool name or
        # a free-text statement that a project exists.
        project = "file_read" in registry.names and isinstance(
            registry.get("file_read"), ProjectFileReadTool
        )
        exists, file_type = None, None
        if frozen_inputs is not None:
            try:
                manifest = InputSet.model_validate(frozen_inputs)
            except ValueError:
                raise MemoryError("skill_facts_unavailable") from None
            exists = bool(manifest.files)
            extensions = {
                PurePosixPath(item.path).suffix.lower().lstrip(".") for item in manifest.files
            }
            if len(extensions) == 1:
                file_type = next(iter(extensions)) or None
        return cls(tuple(sorted(registry.names)), project, exists, file_type, task_family)

    def as_mapping(self):
        return {
            "tool.available": self.tools,
            "project.available": self.project_available,
            "input.exists": self.input_exists,
            "input.file_type": self.input_file_type,
            "task.family": self.task_family,
        }


class SkillApplicabilityEvaluator:
    def assess(self, definition, facts):
        if definition.applicability is None:
            return ApplicabilityDecision("applicable")
        return assess(definition.applicability, facts.as_mapping())
