"""独立评测协调 Worker；普通 Agent 任务仍由 evoagent-worker 执行。"""

import asyncio
from contextlib import suppress

from evoagent.config import Settings
from evoagent.db.session import Database
from evoagent.evals.coordinator import EvalCoordinator
from evoagent.evals.validators import default_validator_registry


async def run_eval_worker() -> None:
    settings = Settings()
    async with Database(settings.database_url.get_secret_value()) as database:
        coordinator = EvalCoordinator(
            database.session_factory,
            default_validator_registry(),
            lease_seconds=settings.eval_lease_seconds,
        )
        while True:
            lease = await coordinator.claim_next(f"{settings.worker_id}-eval")
            if lease is None:
                await asyncio.sleep(settings.eval_poll_seconds)
                continue
            completed = await coordinator.run_once(lease)
            while not completed:
                await asyncio.sleep(settings.eval_poll_seconds)
                lease = await coordinator.heartbeat(lease)
                completed = await coordinator.run_once(lease)


def main() -> None:
    with suppress(KeyboardInterrupt):
        asyncio.run(run_eval_worker())


if __name__ == "__main__":
    main()
