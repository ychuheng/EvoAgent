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
