"""在专用实验队列上启动真实 Worker 进程并导出冻结报告。"""

import argparse
import asyncio
import hashlib
import json
import os
import platform
import sys
from pathlib import Path

from evoagent.config import Settings
from evoagent.db.session import Database
from evoagent.evals.datasets import EvalDatasetService, load_dataset_definition
from evoagent.evals.runtime import RuntimeExperimentService
from evoagent.evals.runtime_schema import RuntimeArm, RuntimeExperimentSpec


async def run(args):
    settings = Settings()
    definition = load_dataset_definition(
        Path(args.dataset.name), root=args.dataset.resolve().parent
    )
    async with Database(settings.database_url.get_secret_value()) as database:
        datasets = EvalDatasetService(database.session_factory)
        dataset = await datasets.import_definition(definition)
        if dataset.status.value == "draft":
            dataset = await datasets.freeze(dataset.id)
        variants = {
            "context_policy": (RuntimeArm(context_policy="legacy"), RuntimeArm()),
            "memory_mode": (RuntimeArm(), RuntimeArm(memory_mode="read_only")),
            "retrieval_backend": (
                RuntimeArm(memory_mode="read_only"),
                RuntimeArm(memory_mode="read_only", retrieval_backend="hybrid"),
            ),
            "worker_count": (RuntimeArm(), RuntimeArm(worker_count=2)),
        }
        control, treatment = variants[args.variable]
        source_root = Path(__file__).resolve().parents[1]
        source_digest = hashlib.sha256()
        for path in sorted(source_root.rglob("*.py")):
            source_digest.update(path.relative_to(source_root).as_posix().encode())
            source_digest.update(path.read_bytes())
        spec = RuntimeExperimentSpec(
            experiment_variable=args.variable,
            control=control,
            treatment=treatment,
            repeats=args.repeats,
            provider=settings.provider.value,
            provider_thinking_mode=settings.provider_thinking_mode,
            context_window_tokens=settings.context_window_tokens,
            max_output_tokens=settings.max_output_tokens,
            max_iterations=settings.max_iterations,
            max_total_tokens=settings.max_total_tokens,
            context_safety_margin=settings.context_safety_margin,
            model=settings.model or "mock-model",
            code_version=settings.code_version,
            dataset_hash=dataset.content_hash,
            embedding_model=settings.embedding_model,
            environment={
                "os": platform.platform(),
                "python": platform.python_version(),
                "cpu_count": str(os.cpu_count()),
                "resource_limits": args.resources,
                "source_sha256": source_digest.hexdigest(),
            },
        )
        service = RuntimeExperimentService(database.session_factory)
        experiment = await service.create(dataset.id, spec)
        for arm in ("control", "treatment"):
            await service.release(experiment.id, arm)
            processes = []
            try:
                for _ in range(getattr(spec, arm).worker_count):
                    environment = {
                        **os.environ,
                        "EVOAGENT_WORKER_RUNTIME_EXPERIMENT_ID": str(experiment.id),
                        "EVOAGENT_WORKER_RUNTIME_ARM": arm,
                        "EVOAGENT_WORKER_CONCURRENCY": "1",
                    }
                    processes.append(
                        await asyncio.create_subprocess_exec(
                            sys.executable,
                            "-c",
                            "from evoagent.workers.bootstrap import main; main()",
                            env=environment,
                        )
                    )
                async with asyncio.timeout(args.timeout):
                    from sqlalchemy import select

                    from evoagent.db.models import RunRecord, RuntimeEvalRunRecord
                    from evoagent.evals.coordinator import _RUN_TERMINAL

                    while True:
                        if any(process.returncode is not None for process in processes):
                            raise RuntimeError("experiment worker exited; inspect retained runs")
                        async with database.session_factory() as db:
                            statuses = tuple(
                                await db.scalars(
                                    select(RunRecord.status)
                                    .join(
                                        RuntimeEvalRunRecord,
                                        RuntimeEvalRunRecord.run_id == RunRecord.id,
                                    )
                                    .where(
                                        RuntimeEvalRunRecord.experiment_id == experiment.id,
                                        RuntimeEvalRunRecord.arm == arm,
                                    )
                                )
                            )
                        if all(status in _RUN_TERMINAL for status in statuses):
                            break
                        await asyncio.sleep(0.2)
            finally:
                for process in processes:
                    if process.returncode is None:
                        process.terminate()
                await asyncio.gather(*(process.wait() for process in processes))
        report = await service.collect(experiment.id)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"{report['report_kind']} report: {args.output.resolve()}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path)
    parser.add_argument(
        "--variable",
        required=True,
        choices=("context_policy", "memory_mode", "retrieval_backend", "worker_count"),
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--timeout", type=float, default=600)
    parser.add_argument("--resources", default="host process; shared resource quota")
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
