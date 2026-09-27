import pytest
from pydantic import ValidationError

from evoagent.core.models import ToolRisk
from evoagent.skills.canonical import canonical_json, content_hash
from evoagent.skills.schema import SkillDefinition
from evoagent.skills.validation import SkillDefinitionValidator, SkillValidationError
from evoagent.tools.builtin.web_search import MockSearchProvider, WebSearchTool
from evoagent.tools.registry import ToolRegistry


def definition_data() -> dict:
    return {
        "schema_version": 1,
        "name": "research_projects",
        "description": "搜索并比较项目",
        "triggers": ["项目调研"],
        "inputs": {
            "topic": {"type": "string"},
            "repositories": {"type": "array", "item_type": "string"},
        },
        "preconditions": {"allowed_tools": ["web_search"], "max_effective_risk": "R1"},
        "steps": [
            {
                "id": "search",
                "action": "tool",
                "tool": "web_search",
                "foreach": "${inputs.repositories}",
                "args": {"query": "${item} ${inputs.topic}"},
            },
            {
                "id": "report",
                "action": "model",
                "depends_on": ["search"],
                "instruction": "根据 ${steps.search.output} 总结",
            },
        ],
        "success_criteria": ["覆盖全部项目"],
        "validators": ["covers_items"],
    }


def validator() -> SkillDefinitionValidator:
    registry = ToolRegistry([WebSearchTool(MockSearchProvider([]))])
    return SkillDefinitionValidator(
        registry,
        allowed_tools=frozenset({"web_search"}),
        max_risk=ToolRisk.R1,
    )


def test_skill_definition_is_strict_and_has_stable_hash() -> None:
    definition = SkillDefinition.model_validate(definition_data())
    result = validator().validate(definition)

    assert result.ordered_step_ids == ("search", "report")
    assert canonical_json({"b": 2, "a": 1}) == '{"a":1,"b":2}'
    assert content_hash(definition.model_dump(mode="json")) == content_hash(
        definition.model_dump(mode="json")
    )
    with pytest.raises(ValidationError):
        SkillDefinition.model_validate({**definition_data(), "unexpected": True})


@pytest.mark.parametrize(
    "mutation",
    [
        lambda data: data.update(schema_version=2),
        lambda data: data["preconditions"].update(allowed_tools=["shell"]),
        lambda data: data["steps"][0].update(args={"path": "C:\\secret.txt"}),
        lambda data: data["steps"][0].update(args={"query": "${inputs.unknown}"}),
        lambda data: data["steps"][0].update(args={"query": "${item}"}, foreach=None),
        lambda data: data["steps"][0].update(depends_on=["report"]),
    ],
)
def test_skill_semantic_validation_rejects_unsafe_or_invalid_graph(mutation) -> None:
    data = definition_data()
    mutation(data)
    definition = SkillDefinition.model_validate(data)

    with pytest.raises(SkillValidationError):
        validator().validate(definition)


def test_skill_definition_accepts_s6_annotations() -> None:
    """M6 S-02 的停止条件、审批点与反例都要能用，且审批点必须指向真实步骤。"""

    definition = SkillDefinition.model_validate(
        {
            "schema_version": 1,
            "name": "annotated_skill",
            "description": "带停止条件、审批点与反例的候选",
            "triggers": ["读项目"],
            "preconditions": {"allowed_tools": ["list_dir"], "max_effective_risk": "R0"},
            "steps": [
                {"id": "scan", "action": "tool", "tool": "list_dir", "args": {"path": "."}},
                {"id": "write", "action": "model", "instruction": "写模块地图"},
            ],
            "success_criteria": ["给出路径依据"],
            "validators": ["run_completed"],
            "stop_conditions": ["连续两次工具报错即停止并报告"],
            "approval_points": [
                {"step_id": "write", "condition": "需要写入文件时", "reason": "写操作需人工确认"}
            ],
            "counterexamples": [
                {"situation": "仓库没有源码", "why_not": "该 Skill 假设存在可读源码"}
            ],
        }
    )

    assert definition.stop_conditions == ("连续两次工具报错即停止并报告",)
    assert definition.approval_points[0].step_id == "write"
    assert definition.counterexamples[0].situation == "仓库没有源码"


def test_approval_points_must_reference_existing_steps() -> None:
    with pytest.raises(ValidationError, match="approval_points reference unknown steps"):
        SkillDefinition.model_validate(
            {
                "schema_version": 1,
                "name": "bad_skill",
                "description": "审批点指向不存在的步骤",
                "triggers": ["x"],
                "preconditions": {"allowed_tools": ["list_dir"], "max_effective_risk": "R0"},
                "steps": [{"id": "scan", "action": "tool", "tool": "list_dir", "args": {}}],
                "success_criteria": ["x"],
                "validators": ["run_completed"],
                "approval_points": [{"step_id": "missing", "condition": "x", "reason": "y"}],
            }
        )


def test_stop_conditions_must_be_unique() -> None:
    with pytest.raises(ValidationError, match="stop_conditions must be unique"):
        SkillDefinition.model_validate(
            {
                "schema_version": 1,
                "name": "dup_skill",
                "description": "重复停止条件",
                "triggers": ["x"],
                "preconditions": {"allowed_tools": ["list_dir"], "max_effective_risk": "R0"},
                "steps": [{"id": "scan", "action": "tool", "tool": "list_dir", "args": {}}],
                "success_criteria": ["x"],
                "validators": ["run_completed"],
                "stop_conditions": ["same", "same"],
            }
        )


def test_require_s6_annotations_rejects_incomplete_candidates() -> None:
    from evoagent.skills.extraction import (
        MissingSkillAnnotationsError,
        require_s6_annotations,
    )

    base = {
        "schema_version": 1,
        "name": "plain_skill",
        "description": "没有附加说明的候选",
        "triggers": ["x"],
        "preconditions": {"allowed_tools": ["list_dir"], "max_effective_risk": "R0"},
        "steps": [{"id": "scan", "action": "tool", "tool": "list_dir", "args": {}}],
        "success_criteria": ["x"],
        "validators": ["run_completed"],
    }
    with pytest.raises(MissingSkillAnnotationsError, match="stop_conditions"):
        require_s6_annotations(SkillDefinition.model_validate(base))

    with pytest.raises(MissingSkillAnnotationsError, match="counterexamples"):
        require_s6_annotations(
            SkillDefinition.model_validate({**base, "stop_conditions": ["停止"]})
        )

    require_s6_annotations(
        SkillDefinition.model_validate(
            {
                **base,
                "stop_conditions": ["停止"],
                "counterexamples": [{"situation": "s", "why_not": "w"}],
            }
        )
    )
