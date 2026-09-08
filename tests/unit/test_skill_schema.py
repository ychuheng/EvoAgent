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
