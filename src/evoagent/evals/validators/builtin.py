"""首批不依赖模型、可重复执行的确定性验证器。"""

from typing import Any

from evoagent.evals.validators.base import FunctionValidator, ValidatorRegistry
from evoagent.trace.bundle import TraceBundle


def _completed(_: dict[str, Any], trace: TraceBundle):
    passed = trace.status == "completed"
    return passed, {"status": trace.status}, None if passed else "run did not complete"


def _contains_sections(parameters: dict[str, Any], trace: TraceBundle):
    sections = [str(item) for item in parameters.get("sections", [])]
    answer = trace.final_answer or ""
    missing = [section for section in sections if section not in answer]
    return (
        not missing,
        {"required": sections, "missing": missing},
        None if not missing else "required sections are missing",
    )


def _minimum_citations(parameters: dict[str, Any], trace: TraceBundle):
    minimum = int(parameters.get("minimum", 1))
    answer = trace.final_answer or ""
    count = answer.count("http://") + answer.count("https://")
    return (
        count >= minimum,
        {"minimum": minimum, "actual": count},
        None if count >= minimum else "not enough citations",
    )


def _covers_items(parameters: dict[str, Any], trace: TraceBundle):
    items = [str(item) for item in parameters.get("items", [])]
    answer = trace.final_answer or ""
    missing = [item for item in items if item not in answer]
    return (
        not missing,
        {"missing": missing},
        None if not missing else "required items are not covered",
    )


def _artifact_exists(parameters: dict[str, Any], trace: TraceBundle):
    expected_type = parameters.get("type")
    matches = [
        item for item in trace.artifacts if expected_type is None or item["type"] == expected_type
    ]
    return (
        bool(matches),
        {"count": len(matches), "type": expected_type},
        None if matches else "required artifact does not exist",
    )


def _tool_policy(parameters: dict[str, Any], trace: TraceBundle):
    allowed = set(map(str, parameters.get("allowed_tools", [])))
    used = {str(item["tool_name"]) for item in trace.tool_calls}
    forbidden = sorted(used - allowed) if allowed else []
    return (
        not forbidden,
        {"used": sorted(used), "forbidden": forbidden},
        None if not forbidden else "forbidden tool was used",
    )


def _no_unknown_effects(_: dict[str, Any], trace: TraceBundle):
    unknown = [item["id"] for item in trace.tool_effects if item["status"] == "unknown"]
    return (
        not unknown,
        {"unknown_effect_ids": unknown},
        None if not unknown else "run contains unknown effects",
    )


def _max_tool_calls(parameters: dict[str, Any], trace: TraceBundle):
    maximum = int(parameters["maximum"])
    actual = len(trace.tool_calls)
    return (
        actual <= maximum,
        {"maximum": maximum, "actual": actual},
        None if actual <= maximum else "too many tool calls",
    )


def default_validator_registry() -> ValidatorRegistry:
    return ValidatorRegistry(
        tuple(
            FunctionValidator(name, function)
            for name, function in (
                ("run_completed", _completed),
                ("contains_sections", _contains_sections),
                ("minimum_citations", _minimum_citations),
                ("covers_items", _covers_items),
                ("artifact_exists", _artifact_exists),
                ("tool_policy", _tool_policy),
                ("no_unknown_effects", _no_unknown_effects),
                ("max_tool_calls", _max_tool_calls),
            )
        )
    )
