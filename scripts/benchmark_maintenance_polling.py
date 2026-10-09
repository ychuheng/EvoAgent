"""离线空闲维护轮询对照；只创建并删除临时 SQLite 库，不调用模型。"""

import argparse
import asyncio
import hashlib
import json
import tempfile
import time
from pathlib import Path

from sqlalchemy import event

from evoagent.db.base import Base
from evoagent.db.session import Database
from evoagent.memory.maintenance import MaintenanceWorker
from evoagent.trace.artifacts import LocalArtifactStore
from evoagent.workers.maintenance import MaintenanceLane
from evoagent.workers.wakeup import Wakeup


async def measure(root: Path, seconds: float, enabled: bool):
    db = Database(f"sqlite+aiosqlite:///{root / ('on.db' if enabled else 'off.db')}")
    async with db.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    counts = {"sql": 0, "commits": 0, "claim_attempts": 0}

    def statement(*args):
        counts["sql"] += 1

    def commit(*args):
        counts["commits"] += 1

    event.listen(db.engine.sync_engine, "before_cursor_execute", statement)
    event.listen(db.engine.sync_engine, "commit", commit)
    worker = MaintenanceWorker(db.session_factory, LocalArtifactStore(root / "artifacts"))
    stopping = asyncio.Event()
    bus = Wakeup(None, "offline", post_commit_hooks_enabled=enabled)

    class CountedWorker:
        async def run_once(self):
            counts["claim_attempts"] += 1
            return await worker.run_once()

    started = time.perf_counter()
    timer = asyncio.get_running_loop().call_later(seconds, stopping.set)
    try:
        await MaintenanceLane(CountedWorker(), bus, poll_seconds=1, idle_backoff=enabled).run(
            stopping
        )
        return {"enabled": enabled, "elapsed_seconds": time.perf_counter() - started, **counts}
    finally:
        timer.cancel()
        await bus.close()
        await db.dispose()


async def main(args):
    with tempfile.TemporaryDirectory(prefix="evoagent-maintenance-benchmark-") as directory:
        root = Path(directory)
        samples = [await measure(root, args.seconds, enabled) for enabled in (False, True)]
    sources = [
        Path(__file__),
        Path("src/evoagent/workers/maintenance.py"),
        Path("src/evoagent/workers/wakeup.py"),
        Path("src/evoagent/memory/maintenance.py"),
    ]
    report = {
        "fixture": "empty-maintenance-lane",
        "database": "temporary SQLite",
        "paid_calls": 0,
        "seconds_per_arm": args.seconds,
        "samples": samples,
        "source_sha256": {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in sources},
        "limits": (
            "Single lane; idle polling only. Does not measure cancellation or erasure latency."
        ),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf8")
    print(json.dumps(samples, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=float, default=60)
    parser.add_argument("--output", type=Path, required=True)
    options = parser.parse_args()
    if options.seconds <= 0:
        parser.error("seconds must be positive")
    asyncio.run(main(options))
