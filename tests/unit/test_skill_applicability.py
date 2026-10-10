from evoagent.skills.applicability import assess, compare
from evoagent.skills.schema import SkillApplicability, SkillFactCondition


def test_unknown_exclusion_cannot_be_treated_as_safe():
    rule = SkillApplicability(
        task_families=("data",),
        excluded_conditions=(SkillFactCondition(key="input.exists", value=False),),
    )
    assert assess(rule, {"task.family": "data"}).status == "unknown"
    assert assess(rule, {"task.family": "data", "input.exists": True}).status == "applicable"
    assert assess(rule, {"task.family": "data", "input.exists": False}).status == "inapplicable"


def test_required_facts_file_types_and_explicit_tool_membership():
    rule = SkillApplicability(
        task_families=("data",),
        file_types=("csv",),
        required_facts=(
            SkillFactCondition(key="tool.available", op="contains", value="file_read"),
        ),
    )
    facts = {"task.family": "data", "input.file_type": "csv", "tool.available": ("file_read",)}
    assert assess(rule, facts).status == "applicable"
    assert assess(rule, {**facts, "input.file_type": "pdf"}).status == "inapplicable"
    assert assess(rule, {**facts, "tool.available": "file_read_something"}).status == "unknown"
    assert assess(rule, {**facts, "task.family": "research"}).status == "inapplicable"


def test_comparisons_do_not_coerce_guessed_or_wrongly_typed_facts():
    condition = SkillFactCondition(key="input.exists", value=True)
    assert compare(condition, {"input.exists": 1}) is None
    assert compare(condition, {"input.exists": "true"}) is None
    condition = SkillFactCondition(key="input.file_type", op="in", value=("csv", "txt"))
    assert compare(condition, {"input.file_type": "csv"}) is True
    assert compare(condition, {"input.file_type": "pdf"}) is False


def test_host_facts_use_actual_reader_and_manifest_only(tmp_path):
    from evoagent.skills.applicability import VerifiedSkillFacts
    from evoagent.tools.builtin.project_file_read import ProjectFileReadTool
    from evoagent.tools.registry import ToolRegistry

    files = [{"path": "INPUT.CSV", "sha256": "0" * 64, "size_bytes": 0, "kind": "text"}]
    facts = VerifiedSkillFacts.from_runtime(
        ToolRegistry([ProjectFileReadTool(tmp_path)]), {"files": files}, task_family="data"
    )
    assert facts.as_mapping() == {
        "project.available": True,
        "input.exists": True,
        "input.file_type": "csv",
        "task.family": "data",
        "tool.available": ("file_read",),
    }
    # Mixed types are unknown, not an arbitrary first extension.
    mixed = VerifiedSkillFacts.from_runtime(
        ToolRegistry([]), {"files": files + [{**files[0], "path": "other.txt"}]}
    )
    assert mixed.input_file_type is None and mixed.task_family is None
    absent = VerifiedSkillFacts.from_runtime(ToolRegistry([]))
    assert absent.input_exists is None and absent.project_available is False


def test_general_is_an_explicit_family_not_a_wildcard():
    rule = SkillApplicability(task_families=("general",))
    assert assess(rule, {}).status == "unknown"
    assert assess(rule, {"task.family": "data"}).status == "inapplicable"
    assert assess(rule, {"task.family": "general"}).status == "applicable"


def test_corrupt_manifest_error_does_not_echo_input():
    import pytest

    from evoagent.memory.schema import MemoryError
    from evoagent.skills.applicability import VerifiedSkillFacts
    from evoagent.tools.registry import ToolRegistry

    with pytest.raises(MemoryError) as error:
        VerifiedSkillFacts.from_runtime(ToolRegistry([]), {"files": ["PRIVATE-SENTINEL"]})
    assert str(error.value) == "skill_facts_unavailable"
    assert "PRIVATE-SENTINEL" not in str(error.value)
