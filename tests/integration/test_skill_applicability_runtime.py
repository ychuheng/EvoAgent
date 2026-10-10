"""Exercise ordinary selection, freeze revalidation and immutable user categories."""

import pytest
import test_lease_fencing as fencing
from pydantic import ValidationError

from evoagent.config import Settings
from evoagent.core.context import ContextBuilder
from evoagent.db.models import SkillRecord, SkillVersionRecord, TaskRecord
from evoagent.retrieval.sources import load_source
from evoagent.runtime.context_resolver import ContextResolver
from evoagent.skills.applicability import VerifiedSkillFacts
from evoagent.skills.canonical import content_hash
from evoagent.skills.lifecycle import SkillVersionStatus
from evoagent.skills.retrieval import SkillRetrievalService
from evoagent.skills.schema import SkillDefinition
from evoagent.tasks.lease import JobLeaseManager
from evoagent.tasks.lease_guard import LeaseGuard
from evoagent.tasks.service import TaskService
from evoagent.tools.builtin.calculator import CalculatorTool
from evoagent.tools.registry import ToolRegistry

fencing_db = fencing.fencing_db


def body(v2=True):
    data = {
        "name": "calculate_report",
        "description": "calculate report",
        "triggers": ["calculate report"],
        "preconditions": {"allowed_tools": ["calculator"], "max_effective_risk": "R0"},
        "steps": [{"id": "compute", "action": "tool", "tool": "calculator", "args": {}}],
        "success_criteria": ["result"],
        "validators": ["result"],
    }
    if v2:
        data.update(
            schema_version=2,
            rationale="development example",
            stop_conditions=["stop on error"],
            counterexamples=[
                {
                    "situation": "non data task",
                    "why_not": "wrong category",
                    "origin": "hypothetical",
                }
            ],
            applicability={
                "task_families": ["data"],
                "required_facts": [
                    {"key": "tool.available", "op": "contains", "value": "calculator"}
                ],
                "excluded_conditions": [{"key": "project.available", "value": True}],
            },
        )
    return SkillDefinition.model_validate(data).model_dump(mode="json")


async def setup(db, family=None, v2=True):
    service = TaskService(db.session_factory)
    scope = await service.create_session("applicability")
    aggregate = await service.create_task(
        session_id=scope.id,
        goal="calculate report data",
        family=family,
        provider="mock",
        model="mock",
    )
    async with db.session_factory() as session:
        skill = SkillRecord(slug="calculate_report", name="calculate_report", description="report")
        session.add(skill)
        await session.flush()
        definition = body(v2)
        version = SkillVersionRecord(
            skill_id=skill.id,
            version=1,
            schema_version=definition["schema_version"],
            definition=definition,
            content_hash=content_hash(definition),
            extraction_key=content_hash({"skill": str(skill.id)}),
            lifecycle_status=SkillVersionStatus.ACTIVE,
        )
        session.add(version)
        await session.flush()
        skill.active_version_id = version.id
        await session.commit()
    return aggregate, version.id


@pytest.mark.parametrize("family,expected", [(None, False), ("research", False), ("data", True)])
@pytest.mark.parametrize("route", ["bm25", "resolver"])
async def test_user_category_filters_both_routes(fencing_db, tmp_path, family, expected, route):
    aggregate, identity = await setup(fencing_db, family)
    registry = ToolRegistry([CalculatorTool()])
    if route == "bm25":
        service = SkillRetrievalService(fencing_db.session_factory, registry, minimum_score=0)
        matches = await service.select(aggregate.run.id, aggregate.task.goal)
        assert bool(matches) is expected
        assert await service.select(aggregate.run.id, "different goal") == matches
    else:
        lease = await JobLeaseManager(fencing_db.session_factory, lease_seconds=60).claim_next(
            "test"
        )
        resolver = ContextResolver(
            fencing_db.session_factory,
            Settings(
                workspace=tmp_path, retrieval_backend="lexical", retrieval_min_lexical_score=0.001
            ),
            registry,
            LeaseGuard(lease),
            ContextBuilder(),
        )
        result = await resolver.resolve(aggregate.task, aggregate.run)
        assert bool(result.skills) is expected
        assert await resolver.resolve(aggregate.task, aggregate.run) == result
    if expected:
        assert (matches if route == "bm25" else result.skills)[0].document.version_id == identity


async def test_source_revalidation_does_not_bypass_applicability(fencing_db):
    _, identity = await setup(fencing_db, "data")
    facts = VerifiedSkillFacts(("calculator",), False, task_family="data")
    async with fencing_db.session_factory() as session:
        assert await load_source(session, f"skill:{identity}", applicability_facts=facts, lock=True)
        blocked = VerifiedSkillFacts(("calculator",), True, task_family="data")
        assert (
            await load_source(session, f"skill:{identity}", applicability_facts=blocked, lock=True)
            is None
        )


async def test_legacy_v1_does_not_require_new_category(fencing_db):
    aggregate, identity = await setup(fencing_db, v2=False)
    matches = await SkillRetrievalService(
        fencing_db.session_factory, ToolRegistry([CalculatorTool()]), minimum_score=0
    ).select(aggregate.run.id, aggregate.task.goal)
    assert matches[0].document.version_id == identity


async def test_family_is_frozen_and_rejects_unrecognized_labels(fencing_db):
    aggregate, _ = await setup(fencing_db, "data")
    async with fencing_db.session_factory() as session:
        task = await session.get(TaskRecord, aggregate.task.id)
        task.family = "research"
        with pytest.raises(ValueError, match="immutable"):
            await session.commit()
        await session.rollback()
    with pytest.raises(ValidationError):
        await TaskService(fencing_db.session_factory).create_task(
            session_id=aggregate.task.session_id,
            goal="report",
            provider="mock",
            model="mock",
            family="guessed-from-model",
        )
