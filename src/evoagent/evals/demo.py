"""在全新数据库上运行阶段三确定性生命周期演示。"""

import argparse
import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import select

from evoagent.config import Settings
from evoagent.core.context import DEFAULT_SYSTEM_PROMPT
from evoagent.core.models import ToolRisk
from evoagent.db.models import EvalCaseRecord, EvalRunRecord, RunRecord, TaskRecord, TurnRecord
from evoagent.db.session import Database
from evoagent.evals.coordinator import EvalCoordinator
from evoagent.evals.datasets import EvalDatasetService, load_dataset_definition
from evoagent.evals.gates import QualityGate, SkillEvaluationService
from evoagent.evals.lifecycle import EvalRunMode, EvalSplit
from evoagent.evals.metrics import EvaluationReportService
from evoagent.evals.service import SourceValidationService
from evoagent.evals.validators import default_validator_registry
from evoagent.runtime.run_config import RunConfigSnapshot, RunMode, sha256_text
from evoagent.skills.canonical import content_hash
from evoagent.skills.extraction import MockCandidateGenerator, SkillExtractionService
from evoagent.skills.provenance import ProvenanceService
from evoagent.skills.retrieval import SkillRetrievalService
from evoagent.skills.sanitizer import TraceSanitizer
from evoagent.skills.schema import SkillDefinition, SkillPreconditions, ToolStep
from evoagent.skills.service import SkillService
from evoagent.skills.validation import SkillDefinitionValidator
from evoagent.tasks.service import TaskService
from evoagent.tasks.state_machine import PersistentRunStatus, TaskStatus
from evoagent.tools.catalog import default_skill_tool_catalog
from evoagent.tools.policy import PermissionPolicy
from evoagent.trace.artifacts import ArtifactService, LocalArtifactStore


def _candidate(expression: str, description: str) -> SkillDefinition:
    return SkillDefinition(
        name="open_source_research",
        description=description,
        triggers=("开源", "Agent", "项目", "架构", "调研"),
        preconditions=SkillPreconditions(
            allowed_tools=("calculator",), max_effective_risk=ToolRisk.R0
        ),
        steps=(
            ToolStep(
                id="sanity_check",
                tool="calculator",
                args={"expression": expression},
            ),
        ),
        success_criteria=("明确说明事实、调用链和能力边界",),
        validators=("run_completed",),
    )


def _snapshot(
    settings: Settings,
    mode: EvalRunMode | RunMode,
    definition: SkillDefinition | None = None,
    version_id=None,
) -> RunConfigSnapshot:
    run_mode = RunMode(mode.value)
    skill_fields = {}
    if run_mode is RunMode.PINNED_SKILL:
        if definition is None or version_id is None:
            raise ValueError("pinned demo snapshot requires a Skill version")
        definition_hash = content_hash(definition.model_dump(mode="json"))
        skill_fields = {
            "skill_version_id": version_id,
            "skill_content_hash": definition_hash,
            "skill_context_hash": content_hash([definition_hash]),
        }
    registry = default_skill_tool_catalog()
    return RunConfigSnapshot(
        provider="mock",
        model="mock-model",
        system_prompt_hash=sha256_text(DEFAULT_SYSTEM_PROMPT),
        tool_manifest_hash=registry.manifest_hash(),
        policy_hash=PermissionPolicy().manifest_hash(),
        max_iterations=settings.max_iterations,
        max_total_tokens=settings.max_total_tokens,
        max_repeated_tool_calls=settings.max_repeated_tool_calls,
        max_tool_result_chars=settings.max_tool_result_chars,
        model_timeout_seconds=settings.model_timeout_seconds,
        task_timeout_seconds=settings.task_timeout_seconds,
        tool_timeout_seconds=settings.tool_timeout_seconds,
        code_version=settings.code_version,
        skill_retrieval_top_k=settings.skill_retrieval_top_k,
        skill_retrieval_min_score=settings.skill_retrieval_min_score,
        run_mode=run_mode,
        **skill_fields,
    )


async def _training_sources(database: Database, settings: Settings, dataset_id):
    async with database.session_factory() as session:
        cases = tuple(
            await session.scalars(
                select(EvalCaseRecord)
                .where(
                    EvalCaseRecord.dataset_id == dataset_id,
                    EvalCaseRecord.split == EvalSplit.TRAIN,
                )
                .order_by(EvalCaseRecord.case_key)
                .limit(2)
            )
        )
    tasks = TaskService(database.session_factory)
    validation = SourceValidationService(database.session_factory, default_validator_registry())
    results = []
    for case in cases:
        session = await tasks.create_session(f"阶段三来源：{case.case_key}")
        aggregate = await tasks.create_task(
            session_id=session.id,
            goal=str(case.public_input["goal"]),
            provider="mock",
            model="mock-model",
            run_mode=RunMode.BASELINE,
        )
        snapshot = _snapshot(settings, EvalRunMode.BASELINE)
        async with database.session_factory() as db_session:
            task = await db_session.get(TaskRecord, aggregate.task.id)
            run = await db_session.get(RunRecord, aggregate.run.id)
            assert task is not None and run is not None
            task.status = TaskStatus.COMPLETED
            run.status = PersistentRunStatus.COMPLETED
            run.final_answer = "# 总结\n这是一条不含秘密和副作用的确定性训练来源。"
            run.config_snapshot = snapshot.model_dump(mode="json")
            run.config_hash = snapshot.content_hash()
            await db_session.commit()
        results.append(await validation.validate(run_id=run.id, eval_case_id=case.id))
    return tuple(results)


async def _complete_position(
    database: Database,
    settings: Settings,
    experiment_id,
    definition: SkillDefinition,
    version_id,
    position: int,
    *,
    regress_skill: bool,
) -> None:
    now = datetime.now(UTC)
    async with database.session_factory() as session:
        eval_runs = tuple(
            await session.scalars(
                select(EvalRunRecord).where(EvalRunRecord.experiment_id == experiment_id)
            )
        )
        for item in eval_runs:
            if item.metrics.get("position") != position:
                continue
            run = await session.get(RunRecord, item.run_id)
            task = await session.get(TaskRecord, item.task_id)
            assert run is not None and task is not None
            failed = regress_skill and item.mode is EvalRunMode.PINNED_SKILL
            run.status = PersistentRunStatus.FAILED if failed else PersistentRunStatus.COMPLETED
            task.status = TaskStatus.FAILED if failed else TaskStatus.COMPLETED
            run.final_answer = None if failed else "# 总结\n确定性评测回答。"
            run.error_code = "demo_regression" if failed else None
            run.started_at = now
            run.ended_at = now + timedelta(milliseconds=25)
            snapshot = _snapshot(settings, item.mode, definition, version_id)
            run.config_snapshot = snapshot.model_dump(mode="json")
            run.config_hash = snapshot.content_hash()
            session.add(
                TurnRecord(
                    run_id=run.id,
                    sequence=1,
                    status="failed" if failed else "completed",
                    usage={"input_tokens": 20, "output_tokens": 10, "total_tokens": 30},
                )
            )
        await session.commit()


async def _evaluate(
    database: Database,
    settings: Settings,
    dataset_id,
    version_id,
    definition: SkillDefinition,
    artifacts: ArtifactService,
    validator: SkillDefinitionValidator,
    *,
    regress_skill: bool,
):
    validators = default_validator_registry()
    coordinator = EvalCoordinator(
        database.session_factory, validators, lease_seconds=settings.eval_lease_seconds
    )
    reports = EvaluationReportService(database.session_factory)
    evaluation = SkillEvaluationService(
        database.session_factory,
        coordinator,
        reports,
        QualityGate(
            database.session_factory,
            validator,
            validators,
            artifacts,
            minimum_sources=settings.skill_min_sources,
        ),
        artifacts,
    )
    experiment = await evaluation.start(
        skill_version_id=version_id,
        dataset_id=dataset_id,
        provider="mock",
        model="mock-model",
        repeats=1,
        code_version=settings.code_version,
    )
    lease = await coordinator.claim_next("phase3-demo")
    if lease is None:
        raise RuntimeError("demo could not claim its evaluation")
    await _complete_position(
        database,
        settings,
        experiment.id,
        definition,
        version_id,
        1,
        regress_skill=regress_skill,
    )
    await coordinator.run_once(lease)
    await _complete_position(
        database,
        settings,
        experiment.id,
        definition,
        version_id,
        2,
        regress_skill=regress_skill,
    )
    if not await coordinator.run_once(lease):
        raise RuntimeError("demo evaluation did not complete")
    gate, gate_hash = await evaluation.finalize(experiment.id)
    return experiment, gate, gate_hash


async def run_demo(settings: Settings, dataset_path: Path) -> dict[str, object]:
    definition = load_dataset_definition(dataset_path, root=settings.eval_dataset_root)
    registry = default_skill_tool_catalog()
    validator = SkillDefinitionValidator(
        registry,
        allowed_tools=frozenset(settings.skill_allowed_tools),
        max_steps=settings.skill_max_steps,
        max_risk=settings.skill_max_effective_risk,
    )
    async with Database(settings.database_url.get_secret_value()) as database:
        datasets = EvalDatasetService(database.session_factory)
        dataset = await datasets.import_definition(definition)
        if dataset.status.value == "draft":
            dataset = await datasets.freeze(dataset.id)
        sources = await _training_sources(database, settings, dataset.id)
        artifacts = ArtifactService(
            LocalArtifactStore(settings.artifact_root), database.session_factory
        )
        provenance = ProvenanceService(
            database.session_factory,
            artifacts,
            TraceSanitizer(settings.workspace),
            allowed_tools=frozenset(settings.skill_allowed_tools),
            max_risk=settings.skill_max_effective_risk,
        )
        extraction_ids = tuple(item.id for item in sources)

        async def extract(candidate: SkillDefinition):
            return await SkillExtractionService(
                database.session_factory,
                provenance,
                MockCandidateGenerator(candidate),
                validator,
                max_sources=settings.skill_max_sources,
            ).extract(extraction_ids)

        service = SkillService(database.session_factory, validator)
        good = _candidate("1+1", "从公开资料梳理 Agent 架构并明确能力边界")
        first = await extract(good)
        first_experiment, first_gate, _ = await _evaluate(
            database,
            settings,
            dataset.id,
            first.skill_version_id,
            good,
            artifacts,
            validator,
            regress_skill=False,
        )
        skill = await service.get_skill(first.skill_id)
        first_publish = await service.approve(
            version_id=first.skill_version_id,
            expected_lock_version=int(skill["lock_version"]),
            reviewer="phase3-demo",
            reason="确定性 HOLDOUT 门禁通过",
        )

        regressed = _candidate("2+2", "一个故意制造正确性回退的候选")
        second = await extract(regressed)
        second_experiment, second_gate, _ = await _evaluate(
            database,
            settings,
            dataset.id,
            second.skill_version_id,
            regressed,
            artifacts,
            validator,
            regress_skill=True,
        )

        improved = _candidate("3+3", "通过门禁并用于演示发布后回滚的新候选")
        third = await extract(improved)
        third_experiment, third_gate, _ = await _evaluate(
            database,
            settings,
            dataset.id,
            third.skill_version_id,
            improved,
            artifacts,
            validator,
            regress_skill=False,
        )
        skill = await service.get_skill(first.skill_id)
        third_publish = await service.approve(
            version_id=third.skill_version_id,
            expected_lock_version=int(skill["lock_version"]),
            reviewer="phase3-demo",
            reason="发布新的通过版本以演示回滚",
        )
        rollback = await service.rollback(
            skill_id=first.skill_id,
            target_version_id=first.skill_version_id,
            expected_lock_version=third_publish.lock_version,
            reviewer="phase3-demo",
            reason="演示回滚到仍兼容且曾通过门禁的版本",
        )
        tasks = TaskService(database.session_factory)
        session = await tasks.create_session("阶段三 ACTIVE Skill 复用")
        retrieval_task = await tasks.create_task(
            session_id=session.id,
            goal="分析一个开源 Agent 项目的架构",
            provider="mock",
            model="mock-model",
        )
        matches = await SkillRetrievalService(
            database.session_factory,
            registry,
            minimum_score=0.01,
        ).select(retrieval_task.run.id, retrieval_task.task.goal)
        return {
            "dataset_id": str(dataset.id),
            "case_count": len(definition.cases),
            "source_eval_run_ids": [str(item.id) for item in sources],
            "first": {
                "version_id": str(first.skill_version_id),
                "experiment_id": str(first_experiment.id),
                "gate_passed": first_gate.passed,
                "published": first_publish.active_version_id == first.skill_version_id,
            },
            "regression": {
                "version_id": str(second.skill_version_id),
                "experiment_id": str(second_experiment.id),
                "gate_passed": second_gate.passed,
            },
            "rollback": {
                "new_version_id": str(third.skill_version_id),
                "experiment_id": str(third_experiment.id),
                "new_gate_passed": third_gate.passed,
                "active_version_id": str(rollback.active_version_id),
            },
            "retrieval_match_ids": [str(item.document.version_id) for item in matches],
        }


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="运行阶段三确定性完整生命周期演示")
    result.add_argument(
        "--dataset",
        type=Path,
        default=Path("open-source-research-v1.json"),
        help="相对于 EVOAGENT_EVAL_DATASET_ROOT 的数据集",
    )
    return result


def main() -> None:
    arguments = parser().parse_args()
    result = asyncio.run(run_demo(Settings(), arguments.dataset))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
