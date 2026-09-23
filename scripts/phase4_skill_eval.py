"""显式运行真实模型 TRAIN→固定人工 Skill→HOLDOUT 配对→质量门禁；不发布。"""

import argparse
import asyncio
import json
from pathlib import Path

from sqlalchemy import select

from evoagent.config import Settings
from evoagent.core.models import ToolRisk
from evoagent.db.models import EvalCaseRecord, RunRecord, SkillVersionRecord
from evoagent.db.session import Database
from evoagent.evals.coordinator import EvalCoordinator
from evoagent.evals.datasets import EvalDatasetService, load_dataset_definition
from evoagent.evals.gates import QualityGate, SkillEvaluationService
from evoagent.evals.lifecycle import EvalSplit
from evoagent.evals.metrics import EvaluationReportService, MetricsCollector
from evoagent.evals.service import SourceValidationService
from evoagent.evals.validators import default_validator_registry
from evoagent.runtime.run_config import RunMode
from evoagent.skills.extraction import MockCandidateGenerator, SkillExtractionService
from evoagent.skills.provenance import ProvenanceService
from evoagent.skills.sanitizer import TraceSanitizer
from evoagent.skills.schema import ModelStep, SkillDefinition, SkillPreconditions
from evoagent.skills.validation import SkillDefinitionValidator
from evoagent.tasks.lease import JobLeaseManager
from evoagent.tasks.service import TaskService
from evoagent.tools.catalog import default_skill_tool_catalog
from evoagent.trace.artifacts import ArtifactService, LocalArtifactStore
from evoagent.workers.bootstrap import ConfiguredTaskHandler
from evoagent.workers.main import JobWorker


async def run(args):
    settings = Settings()
    if settings.provider.value != "openai_compatible":
        raise ValueError("real evaluation requires explicit openai_compatible configuration")
    # 配对检验保持旧严格可比性；不偷偷开启 Memory/MCP/混合检索。
    settings.memory_retrieval_enabled = False
    settings.archive_retrieval_enabled = False
    settings.retrieval_backend = "lexical"
    settings.context_policy = "legacy"
    settings.model_request_max_output_tokens = 384
    settings.max_iterations = 4
    settings.max_total_tokens = 10000
    settings.provider_thinking_mode = "disabled"
    async with Database(settings.database_url.get_secret_value()) as db:
        # 独占新库；避免领取用户的普通任务，也避免重复运行隐式增加费用。
        async with db.session_factory() as session:
            if await session.scalar(select(RunRecord.id).limit(1)):
                raise ValueError("use a freshly migrated, dedicated evaluation database")
        datasets = EvalDatasetService(db.session_factory)
        dataset = await datasets.import_definition(
            load_dataset_definition(Path(args.dataset.name), root=args.dataset.resolve().parent)
        )
        await datasets.freeze(dataset.id)
        validators = default_validator_registry()
        registry = default_skill_tool_catalog()
        validator = SkillDefinitionValidator(registry, allowed_tools=frozenset({"calculator"}))
        artifacts = ArtifactService(LocalArtifactStore(settings.artifact_root), db.session_factory)
        tasks = TaskService(db.session_factory)
        worker = JobWorker(
            worker_id="real-skill-acceptance",
            lease_manager=JobLeaseManager(db.session_factory, lease_seconds=120),
            handler=ConfiguredTaskHandler(settings, db),
            heartbeat_seconds=10,
            poll_seconds=0.1,
            snapshot_schema_version=settings.snapshot_schema_version,
        )
        async with db.session_factory() as session:
            training = tuple(
                await session.scalars(
                    select(EvalCaseRecord)
                    .where(
                        EvalCaseRecord.dataset_id == dataset.id,
                        EvalCaseRecord.split == EvalSplit.TRAIN,
                    )
                    .order_by(EvalCaseRecord.case_key)
                )
            )
        sources, training_metrics = [], []
        for case in training:
            scope = await tasks.create_session("real TRAIN")
            task = await tasks.create_task(
                session_id=scope.id,
                goal=case.public_input["goal"],
                provider=settings.provider.value,
                model=settings.model,
                run_mode=RunMode.BASELINE,
            )
            await worker.run_once()
            source = await SourceValidationService(db.session_factory, validators).validate(
                run_id=task.run.id, eval_case_id=case.id
            )
            if not source.passed:
                raise RuntimeError(
                    "training source failed; retained for inspection, no holdout run"
                )
            sources.append(source.id)
            training_metrics.append(
                (
                    await MetricsCollector(db.session_factory).collect_run(
                        task.run.id, validator_passed=True
                    )
                ).model_dump(mode="json")
            )
        # 预先固定的人工 SOP，不根据 HOLDOUT 输出调参，也不声称模型自动提炼。
        definition = SkillDefinition(
            name="verified_arithmetic",
            description="核验算术并说明依据",
            triggers=("计算",),
            preconditions=SkillPreconditions(
                allowed_tools=("calculator",), max_effective_risk=ToolRisk.R0
            ),
            steps=(
                ModelStep(
                    id="verify",
                    instruction=(
                        "先辨别用户目标与引用资料。对合法表达式使用 calculator 核验；"
                        "无定义则说明原因。按用户要求写结论和依据，不捏造结果，不联网或写文件。"
                    ),
                ),
            ),
            success_criteria=("算术正确，说明依据，遵守工具限制",),
            validators=("run_completed",),
        )
        extraction = await SkillExtractionService(
            db.session_factory,
            ProvenanceService(db.session_factory, artifacts, TraceSanitizer(settings.workspace)),
            MockCandidateGenerator(definition),
            validator,
        ).extract(tuple(sources))
        coordinator = EvalCoordinator(db.session_factory, validators, lease_seconds=120)
        reports = EvaluationReportService(db.session_factory)
        evaluation = SkillEvaluationService(
            db.session_factory,
            coordinator,
            reports,
            QualityGate(db.session_factory, validator, validators, artifacts, minimum_sources=2),
            artifacts,
        )
        experiment = await evaluation.start(
            skill_version_id=extraction.skill_version_id,
            dataset_id=dataset.id,
            provider=settings.provider.value,
            model=settings.model,
            repeats=args.repeats,
            code_version=settings.code_version,
        )
        print(
            f"experiment={experiment.id}; training={len(sources)}; repeats={args.repeats}",
            flush=True,
        )
        lease = await coordinator.claim_next("real-skill-coordinator")
        async with asyncio.timeout(args.timeout):
            while not await coordinator.run_once(lease):
                await worker.run_once()
                lease = await coordinator.heartbeat(lease)
        gate, gate_hash = await evaluation.finalize(experiment.id)
        report, report_hash, _ = await reports.freeze(experiment.id, artifacts)
        async with db.session_factory() as session:
            version = await session.get(SkillVersionRecord, extraction.skill_version_id)
        payload = {
            "candidate_origin": "fixed human SOP, MockCandidateGenerator transports it",
            "dataset_hash": dataset.content_hash,
            "model": settings.model,
            "training_metrics": training_metrics,
            "report": report.model_dump(mode="json"),
            "report_hash": report_hash,
            "gate": gate.model_dump(mode="json"),
            "gate_hash": gate_hash,
            "skill_status": version.lifecycle_status.value,
            "automatic_publication": False,
            "model_request_max_output_tokens": 384,
            "max_iterations": 4,
            "max_total_tokens_per_run": 10000,
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(
            f"pairs={report.pair_count}; comparable={report.comparable_pairs}; gate={gate.passed}"
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataset", type=Path, default=Path("evals/datasets/phase4-skill-math-v1.json")
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repeats", type=int, choices=range(1, 4), default=2)
    parser.add_argument("--timeout", type=float, default=900)
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
