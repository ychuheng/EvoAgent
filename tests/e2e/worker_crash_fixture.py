"""独立 Worker 进程：仅在指定的真实提交边界暂停，供父进程强制终止。"""

import asyncio
import sys
from pathlib import Path

from evoagent.config import Settings
from evoagent.db.session import Database
from evoagent.runtime.checkpoints import PersistentCheckpointStore
from evoagent.tasks.lease import JobLeaseManager
from evoagent.tools.builtin.file_write import FileWriteTool
from evoagent.tools.effects import PersistentToolMiddleware
from evoagent.workers.bootstrap import ConfiguredTaskHandler
from evoagent.workers.main import JobWorker


async def main():
    root = Path(sys.argv[1])
    mode = sys.argv[2]
    settings = Settings(
        _env_file=None,
        database_url=f"sqlite+aiosqlite:///{root / 'process.db'}",
        workspace=root / "workspace",
        lease_seconds=0.9,
        heartbeat_seconds=0.2,
    )

    async def park():
        (root / "ready").write_text(mode)
        await asyncio.Event().wait()

    save = PersistentCheckpointStore.save
    success = PersistentToolMiddleware.after_success
    invoke = FileWriteTool.invoke

    async def checkpoint(self, state):
        await save(self, state)
        if mode == "readonly":
            await park()

    async def after_success(self, token, content):
        await success(self, token, content)
        if mode == "committed" and token.effect_id is not None:
            await park()

    async def write(self, arguments):
        with (root / "invocations").open("a") as stream:
            stream.write("write\n")
        result = await invoke(self, arguments)
        if mode == "unknown":
            await park()
        return result

    PersistentCheckpointStore.save = checkpoint
    PersistentToolMiddleware.after_success = after_success
    FileWriteTool.invoke = write
    async with Database(settings.database_url.get_secret_value()) as database:
        worker = JobWorker(
            worker_id="same-label",
            lease_manager=JobLeaseManager(
                database.session_factory, lease_seconds=settings.lease_seconds
            ),
            handler=ConfiguredTaskHandler(settings, database),
            heartbeat_seconds=settings.heartbeat_seconds,
            poll_seconds=0.02,
            snapshot_schema_version=settings.snapshot_schema_version,
        )
        await worker.run_once()


if __name__ == "__main__":
    asyncio.run(main())
