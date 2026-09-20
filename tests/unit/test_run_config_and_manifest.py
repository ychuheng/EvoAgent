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


def test_full_selection_order_and_second_skill_change_run_identity():
    first = {"version_id": str(uuid4()), "content_hash": sha256_text("first")}
    second = {"version_id": str(uuid4()), "content_hash": sha256_text("second")}
    a = snapshot(run_mode=RunMode.RETRIEVAL, selected_skills=[first, second])
    b = snapshot(run_mode=RunMode.RETRIEVAL, selected_skills=[first])
    c = snapshot(run_mode=RunMode.RETRIEVAL, selected_skills=[second, first])
    assert len({a.content_hash(), b.content_hash(), c.content_hash()}) == 3
    assert a.comparable_with(b)


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


def test_context_policy_changes_comparison_hash_without_changing_legacy_hash():
    import hashlib
    import json

    old = snapshot()
    legacy = old.model_dump(mode="json")
    legacy.pop("context_policy")
    legacy.pop("schema_version")
    legacy.pop("summarizer")
    legacy.pop("selected_skills")
    legacy.pop("retrieval")
    digest = (
        "sha256:"
        + hashlib.sha256(
            json.dumps(legacy, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
    )
    assert old.content_hash() == digest
    bounded = snapshot(context_policy={"mode": "bounded", "version": 1}, max_output_tokens=100)
    assert not bounded.comparable_with(old)
