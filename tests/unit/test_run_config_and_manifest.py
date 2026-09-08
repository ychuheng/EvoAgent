from uuid import uuid4

import pytest
from pydantic import ValidationError

from evoagent.runtime.run_config import RunConfigSnapshot, RunMode, sha256_text
from evoagent.tools.builtin.calculator import CalculatorTool
from evoagent.tools.registry import ToolRegistry


def snapshot(**changes) -> RunConfigSnapshot:
    values = {
        "provider": "mock",
        "model": "mock-model",
        "system_prompt_hash": sha256_text("system"),
        "tool_manifest_hash": sha256_text("tools"),
        "policy_hash": sha256_text("policy"),
        "max_iterations": 8,
        "max_total_tokens": 1_000,
        "model_timeout_seconds": 10,
        "task_timeout_seconds": 20,
        "tool_timeout_seconds": 5,
        "code_version": "test-commit",
        "run_mode": RunMode.BASELINE,
    }
    values.update(changes)
    return RunConfigSnapshot.model_validate(values)


def test_run_config_hash_is_stable_and_compares_only_skill_variable() -> None:
    baseline = snapshot()
    skill_id = uuid4()
    with_skill = snapshot(
        run_mode=RunMode.PINNED_SKILL,
        skill_version_id=skill_id,
        skill_content_hash=sha256_text("skill"),
        skill_context_hash=sha256_text("skill-context"),
    )

    assert baseline.content_hash() == baseline.content_hash()
    assert baseline.content_hash() != with_skill.content_hash()
    assert baseline.comparable_with(with_skill)
    assert not baseline.comparable_with(snapshot(model="another-model"))


def test_run_config_rejects_partial_or_baseline_skill_identity() -> None:
    with pytest.raises(ValidationError):
        snapshot(skill_version_id=uuid4())
    with pytest.raises(ValidationError):
        snapshot(
            run_mode=RunMode.BASELINE,
            skill_version_id=uuid4(),
            skill_content_hash=sha256_text("skill"),
            skill_context_hash=sha256_text("skill-context"),
        )


def test_tool_manifest_hash_tracks_implementation_version() -> None:
    registry = ToolRegistry([CalculatorTool()])
    first = registry.manifest_hash()
    CalculatorTool.implementation_version = "test-only-version"
    try:
        second = ToolRegistry([CalculatorTool()]).manifest_hash()
    finally:
        CalculatorTool.implementation_version = "1"

    assert first.startswith("sha256:")
    assert first != second
