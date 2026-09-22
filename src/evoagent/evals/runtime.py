"""持久化运行时实验：独立工作区、冻结输入、真实 Task、幂等报告收集。"""

from statistics import mean, median
from uuid import uuid4

from sqlalchemy import select

from evoagent.config import ProviderName
from evoagent.db.models import (
    EvalCaseRecord,
    EvalDatasetRecord,
    MessageRecord,
    RunRecord,
    RuntimeEvalRunRecord,
    RuntimeExperimentRecord,
    SessionRecord,
    TaskRecord,
    WorkspaceRecord,
)
from evoagent.evals.coordinator import _RUN_TERMINAL
from evoagent.evals.lifecycle import DatasetStatus, EvalSplit
from evoagent.evals.metrics import MetricsCollector
from evoagent.evals.runtime_fixtures import seed_memories
from evoagent.evals.runtime_schema import RuntimeExperimentSpec, usage_breakdown
from evoagent.evals.schema import ValidatorSpec
from evoagent.evals.validators import default_validator_registry
from evoagent.sessions.service import append_message, text_hash
from evoagent.skills.canonical import content_hash
from evoagent.tasks.state_machine import PersistentRunStatus, TaskStatus
from evoagent.trace.bundle import TraceBundleService


class RuntimeExperimentService:
    def __init__(self, factory):
        self.factory = factory

    async def create(self, dataset_id, spec: RuntimeExperimentSpec):
        async with self.factory() as db:
            dataset = await db.get(EvalDatasetRecord, dataset_id)
            if dataset is None or dataset.status != DatasetStatus.FROZEN:
                raise ValueError("runtime experiment requires frozen dataset")
            if dataset.content_hash != spec.dataset_hash:
                raise ValueError("dataset hash mismatch")
            cases = tuple(
                await db.scalars(
                    select(EvalCaseRecord)
                    .where(
                        EvalCaseRecord.dataset_id == dataset_id,
                        EvalCaseRecord.split == EvalSplit.HOLDOUT,
                    )
                    .order_by(EvalCaseRecord.case_key)
                )
            )
            if not cases:
                raise ValueError("HOLDOUT cases required")
            snapshot = spec.model_dump(mode="json")
            experiment = RuntimeExperimentRecord(
                dataset_id=dataset_id, spec=snapshot, spec_hash=content_hash(snapshot)
            )
            db.add(experiment)
            await db.flush()
            for repeat in range(spec.repeats):
                for case in cases:
                    order = (
                        ("control", "treatment") if repeat % 2 == 0 else ("treatment", "control")
                    )
                    for arm in order:
                        # 一个样本一个 workspace；不会读取普通会话或另一实验臂的记忆。
                        workspace = WorkspaceRecord(name=f"runtime:{experiment.id}:{uuid4().hex}")
                        db.add(workspace)
                        await db.flush()
                        session = SessionRecord(title=case.case_key, workspace_id=workspace.id)
                        db.add(session)
                        await db.flush()
                        history = case.public_input.get("history", [])
                        await seed_memories(db, session, case.public_input.get("memories", []))
                        for index, message in enumerate(history, 1):
                            from evoagent.core.models import Message

                            parsed = Message.model_validate(message)
                            if parsed.role.value not in ("user", "assistant", "tool"):
                                raise ValueError("fixture history cannot override system")
                            db.add(
                                MessageRecord(
                                    session_id=session.id,
                                    session_sequence=index,
                                    kind="fixture",
                                    role=message["role"],
                                    content=message.get("content") or "",
                                    content_hash=text_hash(message.get("content") or ""),
                                )
                            )
                        session.next_message_sequence = len(history) + 1
                        goal = case.public_input.get("goal")
                        if not isinstance(goal, str) or not goal.strip():
                            raise ValueError("fixture requires public goal")
                        task = TaskRecord(
                            session_id=session.id, goal=goal, status=TaskStatus.PAUSED
                        )
                        db.add(task)
                        await db.flush()
                        run = RunRecord(
                            task_id=task.id,
                            status=PersistentRunStatus.PAUSED,
                            provider=spec.provider,
                            model=spec.model,
                            run_mode="retrieval",
                        )
                        db.add(run)
                        await db.flush()
                        message = await append_message(
                            db, task=task, run=run, kind="goal", role="user", content=goal
                        )
                        task.history_before_sequence = message.session_sequence
                        db.add(
                            RuntimeEvalRunRecord(
                                experiment_id=experiment.id,
                                case_id=case.id,
                                arm=arm,
                                repeat_index=repeat,
                                run_id=run.id,
                            )
                        )
            await db.commit()
            return experiment

    async def release(self, experiment_id, arm):
        """按实验臂放行；worker_count 实验由专用执行器控制真实进程数。"""
        if arm not in {"control", "treatment"}:
            raise ValueError("invalid runtime arm")
        async with self.factory() as db:
            experiment = await db.scalar(
                select(RuntimeExperimentRecord)
                .where(RuntimeExperimentRecord.id == experiment_id)
                .with_for_update()
            )
            if experiment is None or experiment.report is not None:
                raise ValueError("experiment missing or already finalized")
            if content_hash(experiment.spec) != experiment.spec_hash:
                raise ValueError("experiment spec changed")
            rows = await db.execute(
                select(TaskRecord, RunRecord)
                .join(RunRecord, RunRecord.task_id == TaskRecord.id)
                .join(RuntimeEvalRunRecord, RuntimeEvalRunRecord.run_id == RunRecord.id)
                .where(
                    RuntimeEvalRunRecord.experiment_id == experiment_id,
                    RuntimeEvalRunRecord.arm == arm,
                )
                .with_for_update()
            )
            for task, run in rows:
                if task.status == TaskStatus.PAUSED:
                    task.status, run.status = TaskStatus.QUEUED, PersistentRunStatus.QUEUED
            experiment.status = "running"
            await db.commit()

    async def collect(self, experiment_id):
        """验证器只在终态 Trace 上执行；数据库锁内幂等提交已计算的结果。"""
        async with self.factory() as db:
            experiment = await db.get(RuntimeExperimentRecord, experiment_id)
            if experiment is None:
                raise ValueError("experiment missing")
            if experiment.report is not None:
                return experiment.report
            records = tuple(
                await db.scalars(
                    select(RuntimeEvalRunRecord).where(
                        RuntimeEvalRunRecord.experiment_id == experiment_id
                    )
                )
            )
        validators = default_validator_registry()
        for record in records:
            if record.metrics is not None:
                continue
            async with self.factory() as db:
                run = await db.get(RunRecord, record.run_id)
                if run.status not in _RUN_TERMINAL:
                    continue
                case = await db.get(EvalCaseRecord, record.case_id)
            trace = await TraceBundleService(self.factory).build(record.run_id)
            results = [
                validators.run(ValidatorSpec.model_validate(raw), trace)
                for raw in case.private_validators
            ]
            metrics = await MetricsCollector(self.factory).collect_run(
                record.run_id, validator_passed=all(result.passed for result in results)
            )
            async with self.factory() as db:
                current = await db.scalar(
                    select(RuntimeEvalRunRecord)
                    .where(RuntimeEvalRunRecord.id == record.id)
                    .with_for_update()
                )
                if current.metrics is None:
                    current.metrics = metrics.model_dump(mode="json")
                    current.validation_results = [
                        result.model_dump(mode="json") for result in results
                    ]
                    await db.commit()
        async with self.factory() as db:
            experiment = await db.scalar(
                select(RuntimeExperimentRecord)
                .where(RuntimeExperimentRecord.id == experiment_id)
                .with_for_update()
            )
            if experiment.report is not None:
                return experiment.report
            records = tuple(
                await db.scalars(
                    select(RuntimeEvalRunRecord)
                    .where(RuntimeEvalRunRecord.experiment_id == experiment_id)
                    .order_by(
                        RuntimeEvalRunRecord.repeat_index,
                        RuntimeEvalRunRecord.case_id,
                        RuntimeEvalRunRecord.arm,
                    )
                )
            )
            if any(record.metrics is None for record in records):
                return None
            details = []
            for record in records:
                run = await db.get(RunRecord, record.run_id)
                from evoagent.evals.runtime_evidence import evidence_for_run

                evidence = await evidence_for_run(db, record)
                details.append(
                    {
                        "case_id": str(record.case_id),
                        "arm": record.arm,
                        "repeat": record.repeat_index,
                        "metrics": record.metrics,
                        "validation_results": record.validation_results,
                        "run_config": run.config_snapshot,
                        "run_config_hash": run.config_hash,
                        "evidence": evidence,
                        "usage": usage_breakdown(
                            {
                                "main": record.metrics["total_tokens"],
                                "summarizer": 0,
                                "memory_generator": 0,
                                "embedding": evidence["embedding_tokens"],
                            },
                            known_subtotals={"main": evidence["known_main_tokens"]},
                        ),
                    }
                )
            summary = {}
            for arm in ("control", "treatment"):
                rows = [item["metrics"] for item in details if item["arm"] == arm]
                aggregate = {
                    "samples": len(rows),
                    "success_rate": mean(int(row["validator_passed"]) for row in rows),
                }
                for field in ("total_tokens", "tool_calls", "latency_ms"):
                    values = [row[field] for row in rows if row[field] is not None]
                    aggregate[field] = {
                        "known_samples": len(values),
                        "mean": mean(values) if values else None,
                        "median": median(values) if values else None,
                    }
                summary[arm] = aggregate
            from evoagent.evals.runtime_schema import runtime_pair_comparable

            pairs = []
            for control in (row for row in details if row["arm"] == "control"):
                treatment = next(
                    row
                    for row in details
                    if row["arm"] == "treatment"
                    and row["case_id"] == control["case_id"]
                    and row["repeat"] == control["repeat"]
                )
                pairs.append(
                    {
                        "case_id": control["case_id"],
                        "repeat": control["repeat"],
                        "comparable": runtime_pair_comparable(
                            control["run_config"],
                            treatment["run_config"],
                            RuntimeExperimentSpec.model_validate(experiment.spec),
                        ),
                    }
                )
            report = {
                "schema_version": 1,
                "experiment_id": str(experiment_id),
                "report_kind": "mock" if experiment.spec["provider"] == "mock" else "real",
                "spec": experiment.spec,
                "spec_hash": experiment.spec_hash,
                "summary": summary,
                "pairs": pairs,
                "comparable_pairs": sum(pair["comparable"] for pair in pairs),
                "samples": details,
                "conclusion": "descriptive only; no statistical significance or quality claim",
            }
            experiment.report, experiment.report_hash = report, content_hash(report)
            experiment.status = "completed"
            await db.commit()
            return report


async def settings_for_run(factory, run_id, settings):
    async with factory() as db:
        record = await db.scalar(
            select(RuntimeEvalRunRecord).where(RuntimeEvalRunRecord.run_id == run_id)
        )
        if record is None:
            return settings
        experiment = await db.get(RuntimeExperimentRecord, record.experiment_id)
        if content_hash(experiment.spec) != experiment.spec_hash:
            raise ValueError("runtime spec hash mismatch")
        spec = RuntimeExperimentSpec.model_validate(experiment.spec)
        arm = getattr(spec, record.arm)
        return settings.model_copy(
            update={
                "provider": ProviderName(spec.provider),
                "provider_thinking_mode": spec.provider_thinking_mode,
                "model_request_max_output_tokens": spec.max_output_tokens,
                "model": spec.model,
                "code_version": spec.code_version,
                "context_policy": arm.context_policy,
                "retrieval_backend": arm.retrieval_backend,
                "memory_retrieval_enabled": arm.memory_mode == "read_only",
                "archive_retrieval_enabled": False,
                "skill_retrieval_top_k": 0,
                "context_window_tokens": spec.context_window_tokens,
                "max_output_tokens": spec.max_output_tokens,
                "context_safety_margin": spec.context_safety_margin,
                "max_total_tokens": spec.max_total_tokens,
                "max_iterations": spec.max_iterations,
                "embedding_model": spec.embedding_model,
                "memory_retrieval_top_k": spec.memory_retrieval_top_k,
                "retrieval_min_lexical_score": spec.retrieval_min_lexical_score,
                "retrieval_max_vector_distance": spec.retrieval_max_vector_distance,
                "retrieval_rrf_k": spec.retrieval_rrf_k,
                "retrieval_memory_budget": spec.retrieval_memory_budget,
            }
        )


async def history_for_run(factory, run_id):
    from evoagent.core.context_policy import MessageGroupBuilder
    from evoagent.core.models import Message

    async with factory() as db:
        record = await db.scalar(
            select(RuntimeEvalRunRecord).where(RuntimeEvalRunRecord.run_id == run_id)
        )
        if record is None:
            return ()
        case = await db.get(EvalCaseRecord, record.case_id)
        messages = tuple(
            Message.model_validate(item) for item in case.public_input.get("history", [])
        )
        MessageGroupBuilder.build(messages)
        return messages
