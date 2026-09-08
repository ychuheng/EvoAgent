from pathlib import Path

import pytest
from sqlalchemy import select

from evoagent.core.models import ToolRisk
from evoagent.db.base import Base
from evoagent.db.models import EvalCaseRecord, SkillRecord, SkillSourceRecord, SkillVersionRecord
from evoagent.db.session import Database
from evoagent.evals.datasets import EvalDatasetService
from evoagent.evals.lifecycle import EvalSplit
from evoagent.evals.schema import EvalCaseDefinition, EvalDatasetDefinition, ValidatorSpec
from evoagent.evals.service import SourceValidationError, SourceValidationService
from evoagent.evals.validators import default_validator_registry
from evoagent.runtime.run_config import RunConfigSnapshot, RunMode
from evoagent.skills.extraction import MockCandidateGenerator, SkillExtractionService
from evoagent.skills.lifecycle import SkillVersionStatus
from evoagent.skills.provenance import ProvenanceService
from evoagent.skills.retrieval import SkillRetrievalService
from evoagent.skills.sanitizer import TraceSanitizer
from evoagent.skills.schema import SkillDefinition, SkillPreconditions, ToolStep
from evoagent.skills.validation import SkillDefinitionValidator
from evoagent.tasks.service import TaskService
from evoagent.tasks.state_machine import PersistentRunStatus
from evoagent.tools.builtin.calculator import CalculatorTool
from evoagent.tools.registry import ToolRegistry
from evoagent.trace.artifacts import ArtifactService, LocalArtifactStore


@pytest.mark.asyncio
async def test_source_to_draft_and_active_retrieval_pipeline(tmp_path: Path) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'phase3.db'}")
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    task_service = TaskService(database.session_factory)
    session = await task_service.create_session("phase three")
    aggregate = await task_service.create_task(
        session_id=session.id, goal="生成数学报告", provider="mock", model="mock-model"
    )
    async with database.session_factory() as db_session:
        run = await db_session.get(type(aggregate.run), aggregate.run.id)
        assert run is not None
        run.status = PersistentRunStatus.COMPLETED
        run.final_answer = "# 总结\n数学结果是 2。"
        snapshot = RunConfigSnapshot(
            provider="mock",
            model="mock-model",
            system_prompt_hash="sha256:" + "1" * 64,
            tool_manifest_hash="sha256:" + "2" * 64,
            policy_hash="sha256:" + "3" * 64,
            max_iterations=8,
            max_total_tokens=1000,
            model_timeout_seconds=10,
            task_timeout_seconds=20,
            tool_timeout_seconds=5,
            code_version="test",
            run_mode=RunMode.RETRIEVAL,
        )
        run.config_snapshot = snapshot.model_dump(mode="json")
        run.config_hash = snapshot.content_hash()
        await db_session.commit()

    dataset_definition = EvalDatasetDefinition(
        name="phase3_smoke",
        version=1,
        cases=(
            EvalCaseDefinition(
                case_key="math_report",
                task_family="report",
                split="train",
                public_input={"goal": "生成报告"},
                private_validators=(
                    ValidatorSpec(name="run_completed"),
                    ValidatorSpec(name="contains_sections", parameters={"sections": ["总结"]}),
                ),
            ),
            EvalCaseDefinition(
                case_key="hidden_math_report",
                task_family="report",
                split="holdout",
                public_input={"goal": "留出任务"},
                private_validators=(ValidatorSpec(name="run_completed"),),
            ),
        ),
    )
    datasets = EvalDatasetService(database.session_factory)
    dataset = await datasets.import_definition(dataset_definition)
    await datasets.freeze(dataset.id)
    async with database.session_factory() as db_session:
        case = (
            await db_session.scalars(
                select(EvalCaseRecord).where(EvalCaseRecord.split == EvalSplit.TRAIN)
            )
        ).one()
        holdout = (
            await db_session.scalars(
                select(EvalCaseRecord).where(EvalCaseRecord.split == EvalSplit.HOLDOUT)
            )
        ).one()

    validation = SourceValidationService(database.session_factory, default_validator_registry())
    with pytest.raises(SourceValidationError, match="TRAIN"):
        await validation.validate(run_id=aggregate.run.id, eval_case_id=holdout.id)
    eval_run = await validation.validate(run_id=aggregate.run.id, eval_case_id=case.id)
    assert eval_run.passed is True

    artifacts = ArtifactService(
        LocalArtifactStore(tmp_path / "artifacts"), database.session_factory
    )
    provenance = ProvenanceService(database.session_factory, artifacts, TraceSanitizer(tmp_path))
    definition = SkillDefinition(
        name="math_report",
        description="生成数学报告",
        triggers=("数学", "报告"),
        preconditions=SkillPreconditions(
            allowed_tools=("calculator",), max_effective_risk=ToolRisk.R0
        ),
        steps=(ToolStep(id="calculate", tool="calculator", args={"expression": "1+1"}),),
        success_criteria=("给出结果",),
        validators=("run_completed",),
    )
    registry = ToolRegistry((CalculatorTool(),))
    extraction = SkillExtractionService(
        database.session_factory,
        provenance,
        MockCandidateGenerator(definition),
        SkillDefinitionValidator(registry, allowed_tools=frozenset({"calculator"})),
    )
    first = await extraction.extract((eval_run.id,))
    second = await extraction.extract((eval_run.id,))
    assert first.created is True
    assert second.created is False
    async with database.session_factory() as db_session:
        version = await db_session.get(SkillVersionRecord, first.skill_version_id)
        skill = await db_session.get(SkillRecord, first.skill_id)
        assert version is not None and skill is not None
        assert len(tuple(await db_session.scalars(select(SkillSourceRecord)))) == 1
        version.lifecycle_status = SkillVersionStatus.ACTIVE
        skill.active_version_id = version.id
        await db_session.commit()

    retrieval_task = await task_service.create_task(
        session_id=session.id, goal="请生成一份数学报告", provider="mock", model="mock-model"
    )
    matches = await SkillRetrievalService(
        database.session_factory, registry, minimum_score=0.01
    ).select(retrieval_task.run.id, retrieval_task.task.goal)
    assert matches[0].document.version_id == first.skill_version_id
    async with database.session_factory() as db_session:
        version = await db_session.get(SkillVersionRecord, first.skill_version_id)
        assert version is not None
        version.definition = {"tampered": True}
        with pytest.raises(ValueError, match="immutable"):
            await db_session.commit()
        await db_session.rollback()
    await database.dispose()
