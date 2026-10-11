"""Offline persistent-task I/O matrix; never load product connection settings.

Cold means a new worker/connection pool, not a flushed OS or PostgreSQL cache.
The Skill fixture uses synthetic TRAIN admission metadata, never efficacy claims.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import event, select
from sqlalchemy import inspect as inspect_database
from sqlalchemy.engine import make_url

from evoagent.config import Settings
from evoagent.core.context import ContextBuilder
from evoagent.core.models import FinishReason, Message, MessageRole, ModelResponse, ToolCall
from evoagent.db.base import Base
from evoagent.db.models import (
    EvalCaseRecord,
    EvalDatasetRecord,
    EvalExperimentRecord,
    EvalRunRecord,
    MessageRecord,
    RunEventRecord,
    RunRecord,
    RunSkillSelectionRecord,
    SessionRecord,
    SkillRecord,
    SkillSourceRecord,
    SkillVersionRecord,
    ToolCallRecord,
    ToolEffectRecord,
)
from evoagent.db.session import Database
from evoagent.evals.gates import GateReport
from evoagent.projects.schema import ProjectAuthorization
from evoagent.projects.service import ProjectService
from evoagent.providers.mock import MockProvider
from evoagent.runtime.persistent_runner import PersistentAgentRunner
from evoagent.runtime.run_config import RunMode
from evoagent.skills.canonical import content_hash
from evoagent.skills.provenance import ProvenanceService
from evoagent.skills.sanitizer import TraceSanitizer
from evoagent.skills.schema import SkillDefinition
from evoagent.tasks.lease import JobLeaseManager
from evoagent.tasks.service import TaskService
from evoagent.tools.builtin.project_edit import EditFileTool
from evoagent.tools.builtin.project_file_read import ProjectFileReadTool
from evoagent.tools.registry import ToolRegistry
from evoagent.trace.artifacts import ArtifactService, LocalArtifactStore
from evoagent.workers.main import JobWorker

if __package__:
    from scripts.runtime_performance_baseline import percentile
else:
    from runtime_performance_baseline import percentile

FIXTURE = "Original benchmark text.\n"
EDITED = "Verified benchmark text.\n"
GOAL = "核验输入，读取 README.md 并检查文字内容。"
FLAGS = (
    "runtime_event_batching_enabled",
    "runtime_recovery_scan_decoupled_enabled",
    "runtime_heartbeat_status_merge_enabled",
    "runtime_snapshot_deduplication_enabled",
    "runtime_scan_cache_enabled",
)
FAMILIES = ("plain", "read_edit_recheck", "enabled_skill", "learning_off_history")


class BenchmarkFailure(RuntimeError):
    def __init__(self, report):
        super().__init__("offline task benchmark failed; safe partial evidence retained")
        self.report = report


async def measure_fault_contracts(url):
    """Run bounded real-process fault contracts on a separate, empty test DB.

    Test durations are reported as contract timings, never task-latency percentiles.
    These fixtures include SIGKILL/recovery, real Redis refusal and blocked learning.
    """
    parsed = validate_database_url(url)
    if parsed.get_backend_name() != "postgresql":
        raise ValueError("fault contracts require a separate isolated PostgreSQL database")
    db = Database(url)
    try:
        async with db.engine.connect() as connection:
            tables = await connection.run_sync(
                lambda conn: inspect_database(conn).get_table_names()
            )
            if tables:
                raise ValueError("fault contract database must have no existing tables")
    finally:
        await db.dispose()
    if __package__:
        from scripts.phase4_demos import read_cases
    else:
        from phase4_demos import read_cases
    root = Path(__file__).resolve().parents[1]
    cases = (
        "tests/e2e/test_worker_process_recovery.py::test_api_task_survives_killed_worker",
        "tests/integration/test_multiworker.py::test_two_real_worker_processes_same_label",
        "tests/integration/test_phase4_demos.py::test_demo_real_redis_outage_keeps_db_task",
        "tests/integration/test_learning_worker.py::test_cleanup_lane_is_not_blocked_by_candidate_generation",
        "tests/integration/test_recovery_scanning.py::test_two_scanners_do_not_duplicate_decisions[postgres]",
    )
    with tempfile.TemporaryDirectory(prefix="evoagent-io-faults-") as directory:
        xml = Path(directory) / "faults.xml"
        started = time.perf_counter()
        result = await asyncio.to_thread(
            subprocess.run,
            [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                *cases,
                f"--junitxml={xml}",
                "-o",
                "junit_family=legacy",
            ],
            cwd=root,
            env={**os.environ, "EVOAGENT_TEST_DATABASE_URL": url, "EVOAGENT_PROVIDER": "mock"},
            capture_output=True,
            text=True,
            encoding="utf8",
            timeout=600,
        )
        # Never copy raw errors, SQL parameters or test credentials into reports.
        samples = read_cases(xml) if xml.exists() else []
        return {
            "returncode": result.returncode,
            "elapsed_seconds": time.perf_counter() - started,
            "cases": samples,
            "complete": bool(samples)
            and result.returncode == 0
            and all(case["status"] == "passed" for case in samples),
            "measurement_kind": "separate fault contract tests, not latency percentiles",
            "paid_calls": 0,
        }


def validate_database_url(url):
    parsed = make_url(url)
    if parsed.get_backend_name() == "postgresql":
        if not (parsed.database or "").startswith("evoagent_perf_"):
            raise ValueError("requires an isolated evoagent_perf_* database")
    elif parsed.get_backend_name() == "sqlite":
        path = Path(parsed.database or "")
        if not path.name.startswith("evoagent_perf_") or path.exists():
            raise ValueError("requires a new evoagent_perf_* SQLite file")
    else:
        raise ValueError("unsupported benchmark database")
    return parsed


def responses(family):
    calls = (
        []
        if family == "plain"
        else [ToolCall(call_id="read", name="file_read", arguments={"path": "README.md"})]
    )
    result = []
    if calls:
        result.append(
            ModelResponse(
                message=Message(role=MessageRole.ASSISTANT, tool_calls=tuple(calls)),
                finish_reason=FinishReason.TOOL_CALLS,
            )
        )
    if family == "read_edit_recheck":
        for call in (
            ToolCall(
                call_id="edit",
                name="edit_file",
                arguments={
                    "path": "README.md",
                    "old_text": FIXTURE,
                    "replacement": EDITED,
                    "expected_sha256": hashlib.sha256(FIXTURE.encode()).hexdigest(),
                },
            ),
            ToolCall(call_id="recheck", name="file_read", arguments={"path": "README.md"}),
        ):
            result.append(
                ModelResponse(
                    message=Message(role=MessageRole.ASSISTANT, tool_calls=(call,)),
                    finish_reason=FinishReason.TOOL_CALLS,
                )
            )
    result.append(
        ModelResponse(
            message=Message(role=MessageRole.ASSISTANT, content="Input checked."),
            finish_reason=FinishReason.STOP,
        )
    )
    return result


class TimedMock(MockProvider):
    """Only time awaiting the provider, excluding downstream persistence work."""

    def __init__(self, sequence):
        super().__init__(sequence)
        self.wait_ns = 0

    async def stream(self, request):
        iterator = super().stream(request).__aiter__()
        while True:
            start = time.perf_counter_ns()
            try:
                item = await anext(iterator)
            except StopAsyncIteration:
                self.wait_ns += time.perf_counter_ns() - start
                break
            self.wait_ns += time.perf_counter_ns() - start
            yield item


async def seed_skill(db, source_task, project, settings):
    """Explicit synthetic admission in an empty disposable benchmark database."""
    definition = SkillDefinition.model_validate(
        {
            "schema_version": 2,
            "name": "benchmark_verify",
            "description": GOAL,
            "triggers": ["核验", "输入", "README.md"],
            "preconditions": {"allowed_tools": ["file_read"], "max_effective_risk": "R0"},
            "steps": [{"id": "verify", "action": "model", "instruction": GOAL}],
            "success_criteria": ["Return observed text"],
            "validators": ["run_completed"],
            "applicability": {"task_families": ["general"]},
            "rationale": "Synthetic benchmark method",
            "stop_conditions": ["Missing input"],
            "counterexamples": [
                {"situation": "Missing file", "why_not": "No evidence", "origin": "hypothetical"}
            ],
        }
    )
    async with db.session_factory() as session:
        dataset = EvalDatasetRecord(name="io-benchmark", version=1, content_hash=content_hash({}))
        skill = SkillRecord(
            name=definition.name, slug=definition.name, description=GOAL, project_id=project.id
        )
        session.add_all([dataset, skill])
        await session.flush()
        case = EvalCaseRecord(
            dataset_id=dataset.id,
            case_key="synthetic_train",
            task_family="general",
            split="train",
            public_input={"goal": GOAL},
            private_validators=[],
        )
        version = SkillVersionRecord(
            skill_id=skill.id,
            version=1,
            schema_version=2,
            definition=definition.model_dump(mode="json"),
            content_hash=content_hash(definition.model_dump(mode="json")),
            extraction_key=content_hash({"benchmark": str(skill.id)}),
            lifecycle_status="active",
        )
        session.add_all([case, version])
        await session.flush()
        experiment = EvalExperimentRecord(
            kind="source_validation",
            dataset_id=dataset.id,
            config_snapshot={"synthetic": True},
            config_hash=content_hash({"synthetic": True}),
            status="completed",
        )
        session.add(experiment)
        await session.flush()
        source = EvalRunRecord(
            experiment_id=experiment.id,
            eval_case_id=case.id,
            task_id=source_task.task.id,
            run_id=source_task.run.id,
            mode="baseline",
            passed=True,
        )
        session.add(source)
        await session.commit()
    artifacts = ArtifactService(LocalArtifactStore(settings.artifact_root), db.session_factory)
    frozen = await ProvenanceService(db.session_factory, artifacts, TraceSanitizer()).freeze(
        source.id
    )
    async with db.session_factory() as session:
        session.add(
            SkillSourceRecord(
                skill_version_id=version.id,
                source_kind="train_eval",
                source_run_id=source_task.run.id,
                source_eval_run_id=source.id,
                trace_artifact_id=frozen.artifact_id,
                source_trace_hash=frozen.source_trace_hash,
            )
        )
        gate_experiment = EvalExperimentRecord(
            kind="skill_comparison",
            dataset_id=dataset.id,
            skill_version_id=version.id,
            config_snapshot={"synthetic": True},
            config_hash=content_hash({"synthetic": True}),
            status="completed",
        )
        session.add(gate_experiment)
        await session.flush()
        report = GateReport(
            skill_version_id=version.id, experiment_id=gate_experiment.id, passed=True, checks=()
        )
        gate_experiment.gate_report = report.model_dump(mode="json")
        gate_experiment.gate_report_hash = report.report_hash()
        (await session.get(SkillVersionRecord, version.id)).gate_report_hash = report.report_hash()
        (await session.get(SkillRecord, skill.id)).active_version_id = version.id
        await session.commit()
    return skill.id


async def measure(url, *, repeats=30, families=FAMILIES, idle_seconds=60):
    parsed = validate_database_url(url)
    if type(repeats) is not int or not 3 <= repeats <= 100:
        raise ValueError("repeats must be 3..100")
    if (
        not families
        or len(set(families)) != len(families)
        or not set(families) <= set(FAMILIES)
        or not 0 <= idle_seconds <= 600
    ):
        raise ValueError("invalid benchmark scenarios")
    db = Database(url)
    sql = Counter()

    def before(_conn, _cursor, statement, _params, context, _many):
        sql["sql_total"] += 1
        sql["sql_" + statement.lstrip().split(maxsplit=1)[0].lower()] += 1
        context._benchmark_started_ns = time.perf_counter_ns()

    def after(_conn, _cursor, _statement, _params, context, _many):
        sql["sql_wait_ns"] += time.perf_counter_ns() - context._benchmark_started_ns

    event.listen(db.engine.sync_engine, "before_cursor_execute", before)
    event.listen(db.engine.sync_engine, "after_cursor_execute", after)
    for name in ("begin", "commit", "rollback"):
        event.listen(
            db.engine.sync_engine,
            name,
            lambda _conn, name=name: sql.update({"transactions_" + name: 1}),
        )
    samples = []
    try:
        async with db.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with db.session_factory() as session:
            if await session.scalar(select(RunRecord.id).limit(1)):
                raise ValueError("benchmark database must contain no existing runs")
        with tempfile.TemporaryDirectory(prefix="evoagent-runtime-io-") as directory:
            root = Path(directory)
            project_root = root / "project"
            project_root.mkdir()
            file = project_root / "README.md"
            file.write_bytes(FIXTURE.encode())
            project = await ProjectService(db.session_factory).register(
                path=str(project_root), authorization=ProjectAuthorization.READ_WRITE
            )
            base = Settings(
                _env_file=None,
                database_url=url,
                provider="mock",
                workspace=root / "artifacts",
                learning_enabled=False,
                personal_trial_enabled=False,
                personal_validation_real_enabled=False,
                retrieval_backend="lexical",
                memory_retrieval_enabled=False,
                archive_retrieval_enabled=False,
                redis_url=None,
                search_provider="mock",
                embedding_model="mock-hash-v1",
                embedding_api_key=None,
                embedding_base_url=None,
                mcp_launch_profiles={},
                mcp_http_profiles={},
                mcp_secret_refs={},
                worker_runtime_experiment_id=None,
                worker_runtime_arm=None,
                runtime_shared_notifications_enabled=False,
                runtime_maintenance_idle_backoff_enabled=False,
                runtime_discovery_history_join_enabled=False,
                **{f: False for f in FLAGS},
            )
            service = TaskService(db.session_factory)
            history_template = ()

            async def execute(family, enabled, chat=None, *, cold=False):
                file.write_bytes(FIXTURE.encode())
                settings = base.model_copy(update={f: enabled for f in FLAGS})
                provider = TimedMock(responses(family))
                registry = ToolRegistry(
                    [
                        ProjectFileReadTool(project_root),
                        EditFileTool(project_root, authorization=ProjectAuthorization.READ_WRITE),
                    ]
                )
                tool_wait = [0]
                for name in registry.names:
                    tool = registry.get(name)
                    original = tool.invoke

                    async def timed(arguments, original=original):
                        start = time.perf_counter_ns()
                        try:
                            return await original(arguments)
                        finally:
                            tool_wait[0] += time.perf_counter_ns() - start

                    tool.invoke = timed
                chat = chat or await service.create_session("runtime I/O fixture")
                if family == "learning_off_history" and history_template:
                    async with db.session_factory() as session:
                        for sequence, (role, content) in enumerate(history_template, 1):
                            session.add(
                                MessageRecord(
                                    session_id=chat.id,
                                    session_sequence=sequence,
                                    role=role,
                                    content=content,
                                    content_hash="sha256:"
                                    + hashlib.sha256(content.encode()).hexdigest(),
                                    kind="legacy",
                                    backfill=True,
                                )
                            )
                        (await session.get(SessionRecord, chat.id)).next_message_sequence = (
                            len(history_template) + 1
                        )
                        await session.commit()
                if cold:
                    await db.engine.dispose()  # New pool, not a server/OS cache flush.
                worker = JobWorker(
                    worker_id="io-benchmark",
                    lease_manager=JobLeaseManager(
                        db.session_factory, lease_seconds=settings.lease_seconds
                    ),
                    handler=PersistentAgentRunner(
                        settings=settings,
                        session_factory=db.session_factory,
                        context_builder=ContextBuilder(),
                        provider=provider,
                        registry=registry,
                    ),
                    heartbeat_seconds=settings.heartbeat_seconds,
                    poll_seconds=0.01,
                    recovery_scan_decoupled=enabled,
                    heartbeat_status_merge=enabled,
                )
                sql.clear()
                started = time.perf_counter_ns()
                task = await service.create_task(
                    session_id=chat.id,
                    goal=GOAL,
                    provider="mock",
                    model="fixed-script",
                    run_mode=RunMode.RETRIEVAL if family == "enabled_skill" else RunMode.BASELINE,
                    project_id=None if family == "plain" else project.id,
                    family="general",
                )
                ack_ms = (time.perf_counter_ns() - started) / 1e6
                assert await worker.run_once(), "benchmark task was not claimed"
                elapsed = (time.perf_counter_ns() - started) / 1e6
                measured = dict(sql)
                async with db.session_factory() as session:
                    run = await session.get(RunRecord, task.run.id)
                    if str(run.status) != "completed":
                        raise BenchmarkFailure(
                            {
                                "schema_version": 1,
                                "complete": False,
                                "paid_calls": 0,
                                "samples": list(samples),
                                "failed_sample": {
                                    "family": family,
                                    "optimized": enabled,
                                    "cache": "cold_pool" if cold else "warm_pool",
                                    "terminal": str(run.status),
                                    "error_code": run.error_code,
                                    "elapsed_ms": elapsed,
                                    "sql": measured,
                                },
                                "note": "Failure retained; no passing percentile is reported.",
                            }
                        )
                    selected = list(
                        await session.scalars(
                            select(RunSkillSelectionRecord).where(
                                RunSkillSelectionRecord.run_id == run.id
                            )
                        )
                    )
                    events = list(
                        await session.scalars(
                            select(RunEventRecord)
                            .where(RunEventRecord.run_id == run.id)
                            .order_by(RunEventRecord.sequence)
                        )
                    )
                    tool_calls = list(
                        await session.scalars(
                            select(ToolCallRecord)
                            .where(ToolCallRecord.run_id == run.id)
                            .order_by(ToolCallRecord.id)
                        )
                    )
                    effects = list(
                        await session.scalars(
                            select(ToolEffectRecord.status)
                            .join(
                                ToolCallRecord, ToolCallRecord.id == ToolEffectRecord.tool_call_id
                            )
                            .where(ToolCallRecord.run_id == run.id)
                        )
                    )
                if family == "enabled_skill":
                    assert selected, "enabled Skill fixture did not actually select a method"
                if family == "read_edit_recheck":
                    assert file.read_bytes() == EDITED.encode(), "edit did not occur"
                    assert len(provider.requests) == 4, "read/edit/recheck sequence was incomplete"
                return task, {
                    "elapsed_ms": elapsed,
                    "queued_event_ack_ms": ack_ms,
                    "provider_wait_ms": provider.wait_ns / 1e6,
                    "tool_wait_ms": tool_wait[0] / 1e6,
                    "sql_wait_ms": measured.pop("sql_wait_ns", 0) / 1e6,
                    **measured,
                    "selected_versions": len(selected),
                    "event_count": len(events),
                    "committed_cursor_contiguous": [e.sequence for e in events]
                    == list(range(1, len(events) + 1)),
                    "config_hash": run.config_hash,
                    "terminal": str(run.status),
                    "tool_names": sorted(c.tool_name for c in tool_calls),
                    "effect_status_counts": dict(Counter(str(status) for status in effects)),
                    "event_types": [e.event_type for e in events],
                }

            skill_id = None
            for family in families:
                history_chat = None
                if family == "enabled_skill":
                    source, _ = await execute("learning_off_history", False)
                    skill_id = await seed_skill(db, source, project, base)
                if family == "learning_off_history":
                    history_chat = await service.create_session("fixed 20-task history")
                    for _ in range(20):
                        await execute("plain", False, history_chat)
                    async with db.session_factory() as session:
                        messages = list(
                            await session.scalars(
                                select(MessageRecord)
                                .where(MessageRecord.session_id == history_chat.id)
                                .order_by(MessageRecord.session_sequence)
                            )
                        )
                        history_template = tuple((m.role, m.content) for m in messages)
                    assert len(history_template) == 40
                    # Each measured task gets exactly the same frozen legacy
                    # conversation, rather than an ever-growing chat that would
                    # eventually test the context limit instead of persistence.
                    history_chat = None
                for cache in ("cold_pool", "warm_pool"):
                    # Equal warmup and alternating arms reduce append-only DB
                    # growth bias; IDs and histories are never reused as evidence.
                    for enabled in (False, True):
                        await execute(family, enabled, history_chat, cold=cache == "cold_pool")
                    for index in range(repeats):
                        for enabled in (False, True) if index % 2 == 0 else (True, False):
                            _, sample = await execute(
                                family, enabled, history_chat, cold=cache == "cold_pool"
                            )
                            samples.append(
                                {
                                    "family": family,
                                    "cache": cache,
                                    "optimized": enabled,
                                    "repeat": index,
                                    **sample,
                                }
                            )
                if skill_id:
                    async with db.session_factory() as session:
                        (await session.get(SkillRecord, skill_id)).status = "disabled"
                        await session.commit()
                    skill_id = None
            idle = []
            if idle_seconds:
                if __package__:
                    from scripts.benchmark_maintenance_polling import measure as measure_idle
                else:
                    from benchmark_maintenance_polling import measure as measure_idle

                for enabled in (False, True):
                    idle.append(await measure_idle(root, idle_seconds, enabled))
        groups = []
        for family in families:
            reference = None
            for row in (r for r in samples if r["family"] == family):
                if not row["committed_cursor_contiguous"]:
                    raise AssertionError("committed event cursor gap")
                semantics = {
                    key: row[key]
                    for key in (
                        "terminal",
                        "selected_versions",
                        "tool_names",
                        "effect_status_counts",
                        "event_types",
                    )
                }
                if reference is None:
                    reference = semantics
                elif reference != semantics:
                    raise AssertionError("benchmark arms differ in persisted execution semantics")
        for family in families:
            for cache in ("cold_pool", "warm_pool"):
                for enabled in (False, True):
                    rows = [
                        r
                        for r in samples
                        if (r["family"], r["cache"], r["optimized"]) == (family, cache, enabled)
                    ]
                    groups.append(
                        {
                            "family": family,
                            "cache": cache,
                            "optimized": enabled,
                            "n": len(rows),
                            **{
                                key + "_p" + str(p): percentile([r[key] for r in rows], p / 100)
                                for key in (
                                    "elapsed_ms",
                                    "sql_total",
                                    "transactions_commit",
                                    "sql_wait_ms",
                                    "provider_wait_ms",
                                    "tool_wait_ms",
                                )
                                for p in (50, 95)
                            },
                        }
                    )
        return {
            "schema_version": 1,
            "created_at": datetime.now(UTC).isoformat(),
            "git_sha": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
            "script_hash": content_hash(Path(__file__).read_text(encoding="utf8")),
            "database": parsed.get_backend_name(),
            "paid_calls": 0,
            "complete": True,
            "repeats": repeats,
            "slo_sample_sufficient": repeats >= 30,
            "fixture_hash": content_hash(FIXTURE),
            "optimized_flags": list(FLAGS),
            "samples": samples,
            "persisted_semantics_equal": True,
            "summaries": groups,
            "idle": idle,
            "limitations": [
                "Mock mechanisms only; synthetic TRAIN Skill admission is not efficacy evidence.",
                "Cold pool resets connections only; both arms assemble fresh workers.",
                "History copies 40 fixed messages from 20 offline tasks into fresh sessions.",
                "Idle numbers use a separate temporary SQLite maintenance lane, not the task DB.",
                "SQL timings exclude commit time; not total DB attribution.",
                "Fault/learning-contention tests are separate from this latency matrix.",
                "Acknowledgement is a DB commit acknowledgement, not browser SSE visibility.",
            ],
        }
    finally:
        await db.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeats", type=int, default=30)
    parser.add_argument("--idle-seconds", type=float, default=60)
    parser.add_argument("--families", nargs="+", choices=FAMILIES, default=list(FAMILIES))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--fault-checks",
        action="store_true",
        help="also test faults in separate empty EVOAGENT_PERF_FAULT_DATABASE_URL",
    )
    args = parser.parse_args()
    url = os.environ.get("EVOAGENT_PERF_DATABASE_URL")
    if not url:
        parser.error("set EVOAGENT_PERF_DATABASE_URL to a new isolated benchmark database")
    try:
        report = asyncio.run(
            measure(
                url,
                repeats=args.repeats,
                families=tuple(args.families),
                idle_seconds=args.idle_seconds,
            )
        )
    except BenchmarkFailure as error:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(error.report, indent=2) + "\n", encoding="utf8")
        raise SystemExit("benchmark failed; partial evidence saved") from None
    report["fault_contracts"] = {"status": "not_requested"}
    if args.fault_checks:
        fault_url = os.environ.get("EVOAGENT_PERF_FAULT_DATABASE_URL")
        if not fault_url or make_url(fault_url) == make_url(url):
            parser.error("fault checks require a distinct empty EVOAGENT_PERF_FAULT_DATABASE_URL")
        report["fault_contracts"] = asyncio.run(measure_fault_contracts(fault_url))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf8")
    print(f"{len(report['samples'])} offline task samples; paid_calls=0; report={args.output}")
    if args.fault_checks and not report["fault_contracts"]["complete"]:
        raise SystemExit("fault contract evidence is incomplete; see report statuses")


if __name__ == "__main__":
    main()
