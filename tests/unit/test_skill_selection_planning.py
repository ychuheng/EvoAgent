"""Deterministic planning tests use synthetic candidates, not source authority."""

from uuid import uuid4

import pytest

from evoagent.config import Settings
from evoagent.skills.applicability import VerifiedSkillFacts
from evoagent.skills.canonical import content_hash
from evoagent.skills.retrieval import SkillDocument
from evoagent.skills.schema import SkillDefinition
from evoagent.skills.selection import SkillCandidate, SkillSelector
from evoagent.skills.selection_snapshot import SkillSelectionScope
from evoagent.tools.builtin.calculator import CalculatorTool
from evoagent.tools.registry import ToolRegistry


def candidate(scope, *, origin="formal", trial_id=None):
    definition = SkillDefinition.model_validate(
        {
            "schema_version": 2,
            "name": "calculate_report",
            "description": "calculate report",
            "triggers": ["calculate report"],
            "preconditions": {"allowed_tools": ["calculator"], "max_effective_risk": "R0"},
            "steps": [{"id": "compute", "action": "tool", "tool": "calculator", "args": {}}],
            "success_criteria": ["result"],
            "validators": ["result"],
            "rationale": "synthetic planning example",
            "stop_conditions": ["stop on error"],
            "counterexamples": [
                {
                    "situation": "unrelated task",
                    "why_not": "wrong category",
                    "origin": "hypothetical",
                }
            ],
            "applicability": {"task_families": ["data"]},
        }
    )
    return SkillCandidate(
        SkillDocument(
            uuid4(), uuid4(), definition, content_hash(definition.model_dump(mode="json"))
        ),
        scope,
        origin,
        trial_id,
    )


def selector():
    scope = SkillSelectionScope(workspace_id=uuid4())
    registry = ToolRegistry([CalculatorTool()])
    instance = SkillSelector(None, Settings(_env_file=None), registry, None, scope)
    facts = VerifiedSkillFacts.from_runtime(registry, task_family="data")
    return instance, scope, facts


async def test_applicability_and_scope_are_filtered_before_lexical_or_vector_ranking():
    instance, scope, facts = selector()
    eligible = candidate(scope)
    unrelated = candidate(SkillSelectionScope(workspace_id=uuid4()))
    for backend in ["bm25", "hybrid"]:
        ranked = await instance.rank(
            "calculate report",
            (eligible, unrelated),
            facts=facts,
            backend=backend,
            distances={unrelated.document.version_id: 0},
        )
        assert len(ranked) == 1 and ranked[0].candidate == eligible
        unknown = VerifiedSkillFacts.from_runtime(instance.registry)
        assert not await instance.rank(
            "calculate report",
            (eligible,),
            facts=unknown,
            backend=backend,
            distances={eligible.document.version_id: 0},
        )


async def test_one_method_is_frozen_with_budget_and_explicit_zero_choice():
    instance, scope, facts = selector()
    ranked = await instance.rank(
        "calculate report", (candidate(scope), candidate(scope)), facts=facts
    )
    choice = instance.choose(ranked, token_budget=2000)
    assert choice.selected is not None and "Skill：calculate_report" in choice.text
    assert instance.choose(ranked, token_budget=0).selected is None
    assert len(instance.choose(ranked, token_budget=0).omissions) == len(ranked)
    assert instance.choose(ranked, token_budget=2000, max_skills=0).text is None
    with pytest.raises(ValueError, match="budget invalid"):
        instance.choose(ranked, token_budget=2000, max_skills=2)


async def test_duplicate_identity_and_invalid_trial_identity_are_rejected():
    instance, scope, facts = selector()
    method = candidate(scope)
    with pytest.raises(ValueError, match="duplicate selection"):
        await instance.rank("calculate report", (method, method), facts=facts)
    with pytest.raises(ValueError, match="trial identity"):
        candidate(scope, origin="trial")
    assert candidate(scope, origin="trial", trial_id=uuid4()).origin == "trial"


async def test_unavailable_tool_cannot_be_promoted_by_vector_evidence():
    instance, scope, facts = selector()
    method = candidate(scope)
    instance.registry = ToolRegistry([])
    assert not await instance.rank(
        "calculate report",
        (method,),
        facts=facts,
        backend="hybrid",
        distances={method.document.version_id: 0},
    )
