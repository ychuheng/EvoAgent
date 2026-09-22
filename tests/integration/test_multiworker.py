import asyncio
import os
import subprocess
import sys
import time

import pytest
from sqlalchemy import select

from evoagent.db.base import Base
from evoagent.db.models import RunEventRecord, TaskRecord
from evoagent.db.session import Database
from evoagent.tasks.service import TaskService
from evoagent.tasks.state_machine import TaskStatus


@pytest.mark.postgres
async def test_two_real_worker_processes_same_label(tmp_path):
    url = os.getenv("EVOAGENT_TEST_DATABASE_URL")
    if not url:
        pytest.skip("EVOAGENT_TEST_DATABASE_URL is not configured")
    database = Database(url)
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)
    service = TaskService(database.session_factory)
    session = await service.create_session("dual worker acceptance")
    for index in range(8):
        await service.create_task(
            session_id=session.id,
            goal=f"offline report {index}",
            provider="mock",
            model="mock-model",
        )
    environment = {
        **os.environ,
        "EVOAGENT_DATABASE_URL": url,
        "EVOAGENT_PROVIDER": "mock",
        "EVOAGENT_WORKER_ID": "same-label",
        "EVOAGENT_WORKER_CONCURRENCY": "1",
        "EVOAGENT_WORKER_POLL_SECONDS": "0.1",
        "EVOAGENT_ARTIFACT_ROOT": str(tmp_path / "artifacts"),
        "EVOAGENT_WORKSPACE": str(tmp_path),
        "EVOAGENT_REDIS_NAMESPACE": "dual-process-test",
    }
    environment["EVOAGENT_REDIS_URL"] = os.getenv("EVOAGENT_TEST_REDIS_URL", "")
    processes = [
        subprocess.Popen(
            [sys.executable, "-c", "from evoagent.workers.bootstrap import main; main()"],
            env=environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        for _ in range(2)
    ]
    started = time.monotonic()
    try:
        async with asyncio.timeout(60):
            while True:
                for process in processes:
                    if process.poll() is not None:
                        pytest.fail(process.stderr.read().decode(errors="replace"))
                async with database.session_factory() as db:
                    tasks = tuple(await db.scalars(select(TaskRecord)))
                    if all(task.status == TaskStatus.COMPLETED for task in tasks):
                        break
                await asyncio.sleep(0.2)
        async with database.session_factory() as db:
            claims = tuple(
                await db.scalars(
                    select(RunEventRecord).where(RunEventRecord.event_type == "worker.claimed")
                )
            )
        owners = {event.payload["worker_id"] for event in claims}
        assert len(owners) == 2
        assert len({event.run_id for event in claims}) == 8
        assert all(task.lease_owner is None for task in tasks)
        assert all(task.lease_epoch >= 1 for task in tasks)
        print(f"real dual-process throughput: {8 / (time.monotonic() - started):.3f} tasks/s")
    finally:
        for process in processes:
            process.terminate()
        for process in processes:
            await asyncio.to_thread(process.wait, 10)
            process.stderr.close()
        async with database.engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)
        await database.dispose()
