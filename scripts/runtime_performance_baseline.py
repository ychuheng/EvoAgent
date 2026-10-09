"""固定只读项目任务的持久化成本基线；仅 Mock，只接受独立验收数据库。"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import os
import platform
import subprocess
import tempfile
import time
import tracemalloc
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import event, select
from sqlalchemy.engine import make_url

from evoagent.config import Settings
from evoagent.core.context import ContextBuilder
from evoagent.core.models import (
    FinishReason,
    Message,
    MessageRole,
    ModelResponse,
    ProviderEvent,
    ProviderEventType,
    ToolCall,
)
from evoagent.db.base import Base
from evoagent.db.models import RunEventRecord, RunRecord, RunSnapshotRecord
from evoagent.db.session import Database
from evoagent.projects.service import ProjectService
from evoagent.providers.mock import MockProvider
from evoagent.runtime.persistent_runner import PersistentAgentRunner
from evoagent.skills.canonical import canonical_json, content_hash
from evoagent.tasks.lease import JobLeaseManager
from evoagent.tasks.service import TaskService
from evoagent.tasks.state_machine import PersistentRunStatus
from evoagent.tools.builtin.project_file_read import ProjectFileReadTool
from evoagent.tools.registry import ToolRegistry
from evoagent.workers.main import JobWorker

FIXTURE = "# Example\nThis project stores notes as UTF-8 text.\n"
GOAL = "Read README.md and explain how this project stores notes."


def percentile(values, fraction):
    return sorted(values)[max(0, math.ceil(len(values) * fraction) - 1)]


async def measure(url: str, *, repeats: int, deltas: int) -> dict:
    parsed = make_url(url)
    if parsed.get_backend_name() == "postgresql":
        if not (parsed.database or "").startswith("evoagent_perf_"):
            raise ValueError("PostgreSQL baseline requires an isolated evoagent_perf_* database")
    elif parsed.get_backend_name() == "sqlite":
        path = Path(parsed.database or "")
        if not path.name.startswith("evoagent_perf_") or path.exists():
            raise ValueError("SQLite baseline requires a new evoagent_perf_* file")
    else:
        raise ValueError("unsupported baseline database")
    database = Database(url)
    metrics = Counter()

    def sql(_connection, _cursor, statement, parameters, _context, _many):
        verb = statement.lstrip().split(maxsplit=1)[0].lower()
        metrics["sql_total"] += 1
        metrics[f"sql_{verb}"] += 1
        # 参数的规范近似字节数，不存内容；不是 PostgreSQL 网络/磁盘实际字节数。
        metrics["parameter_repr_bytes"] += len(repr(parameters).encode())

    event.listen(database.engine.sync_engine, "before_cursor_execute", sql)
    for name in ("begin", "commit", "rollback"):
        event.listen(
            database.engine.sync_engine,
            name,
            lambda _connection, name=name: metrics.update({f"transactions_{name}": 1}),
        )
    rows = []
    tracemalloc.start()
    try:
        async with database.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with database.session_factory() as session:
            if await session.scalar(select(RunRecord.id).limit(1)):
                raise ValueError("baseline database must not contain existing runs")
        with tempfile.TemporaryDirectory(prefix="evoagent-perf-") as directory:
            root = Path(directory)
            project_root = root / "fixture"
            project_root.mkdir()
            (project_root / "README.md").write_text(FIXTURE, encoding="utf-8")
            project = await ProjectService(database.session_factory).register(
                path=str(project_root)
            )
            settings = Settings(
                _env_file=None, database_url=url, workspace=root / "outputs", provider="mock"
            )
            service = TaskService(database.session_factory)
            for index in range(repeats + 1):
                chat = await service.create_session(f"performance baseline {index}")
                provider = MockProvider(
                    [
                        ModelResponse(
                            message=Message(
                                role=MessageRole.ASSISTANT,
                                tool_calls=(
                                    ToolCall(
                                        call_id="read-1",
                                        name="file_read",
                                        arguments={"path": "README.md"},
                                    ),
                                ),
                            ),
                            finish_reason=FinishReason.TOOL_CALLS,
                        ),
                        [
                            *(
                                ProviderEvent(
                                    type=ProviderEventType.TEXT_DELTA, text_delta="notes "
                                )
                                for _ in range(deltas)
                            ),
                            ProviderEvent(
                                type=ProviderEventType.COMPLETED,
                                response=ModelResponse(
                                    message=Message(
                                        role=MessageRole.ASSISTANT, content="notes " * deltas
                                    ),
                                    finish_reason=FinishReason.STOP,
                                ),
                            ),
                        ],
                    ]
                )
                worker = JobWorker(
                    worker_id="baseline",
                    lease_manager=JobLeaseManager(
                        database.session_factory, lease_seconds=settings.lease_seconds
                    ),
                    handler=PersistentAgentRunner(
                        settings=settings,
                        session_factory=database.session_factory,
                        context_builder=ContextBuilder(),
                        provider=provider,
                        registry=ToolRegistry([ProjectFileReadTool(project_root)]),
                    ),
                    heartbeat_seconds=settings.heartbeat_seconds,
                    poll_seconds=0.01,
                )
                metrics.clear()
                started = time.perf_counter()
                task = await service.create_task(
                    session_id=chat.id,
                    goal=GOAL,
                    provider="mock",
                    model="fixed-script",
                    project_id=project.id,
                )
                queued_event_ack_ms = (time.perf_counter() - started) * 1000
                if not await worker.run_once():
                    raise AssertionError("baseline worker did not claim the task")
                elapsed_ms = (time.perf_counter() - started) * 1000
                measured = dict(metrics)
                # 以下证据读取不计入执行 SQL。
                async with database.session_factory() as session:
                    run = await session.get(RunRecord, task.run.id)
                    if run.status != PersistentRunStatus.COMPLETED or len(provider.requests) != 2:
                        raise AssertionError(
                            f"baseline task failed: {run.status} / {run.error_code}"
                        )
                    events = tuple(
                        await session.scalars(
                            select(RunEventRecord).where(RunEventRecord.run_id == run.id)
                        )
                    )
                    snapshots = tuple(
                        await session.scalars(
                            select(RunSnapshotRecord).where(RunSnapshotRecord.run_id == run.id)
                        )
                    )
                row = {
                    "iteration": index,
                    "elapsed_ms": round(elapsed_ms, 3),
                    "queued_event_ack_ms": round(queued_event_ack_ms, 3),
                    **measured,
                    "events": len(events),
                    "snapshots": len(snapshots),
                    "runtime_config_hash": run.config_hash,
                    "snapshot_json_bytes": sum(
                        len(canonical_json(s.state).encode()) for s in snapshots
                    ),
                }
                if index:
                    rows.append(row)
        _, peak = tracemalloc.get_traced_memory()
        revision = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
        return {
            "schema_version": 1,
            "created_at": datetime.now(UTC).isoformat(),
            "git_sha": revision,
            "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "python": platform.python_version(),
            "platform": platform.system(),
            "database": parsed.get_backend_name(),
            "provider": "scripted_mock",
            "paid_calls": 0,
            "family": "project_read",
            "fixture_hash": content_hash(FIXTURE),
            "goal_hash": content_hash(GOAL),
            "script_version": 1,
            "deltas": deltas,
            "warmup_runs": 1,
            "repeats": repeats,
            "python_heap_peak_bytes": peak,
            "elapsed_p50_ms": percentile([r["elapsed_ms"] for r in rows], 0.5),
            "elapsed_p95_ms": percentile([r["elapsed_ms"] for r in rows], 0.95),
            "notes": [
                "Mock only; not evidence of real model latency or Skill benefit",
                "queued_event_ack_ms is service commit acknowledgement, not browser SSE visibility",
                "parameter_repr_bytes is an approximation, not network or storage bytes",
                "small sample percentiles are descriptive, not production SLO proof",
            ],
            "runs": rows,
        }
    finally:
        tracemalloc.stop()
        await database.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeats", type=int, default=10)
    parser.add_argument("--deltas", type=int, default=32)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not 3 <= args.repeats <= 100 or not 1 <= args.deltas <= 256:
        parser.error("repeats must be 3..100; deltas must be 1..256")
    # 明确独立验收 URL；不读取 EVOAGENT_DATABASE_URL 或 .env 中的产品连接。
    url = os.environ.get("EVOAGENT_PERF_DATABASE_URL")
    if not url:
        parser.error("set EVOAGENT_PERF_DATABASE_URL to an empty isolated database")
    report = asyncio.run(measure(url, repeats=args.repeats, deltas=args.deltas))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"{report['database']} baseline: {report['repeats']} runs; "
        f"P95={report['elapsed_p95_ms']:.3f}ms; output={args.output}"
    )


if __name__ == "__main__":
    main()
