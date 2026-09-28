"""Run the API and worker on a trusted Windows host with isolated local data.

The model still receives project tools only for a registered directory. Windows
commands run with the current user's OS rights after a per-call approval; they
are deliberately not described as sandboxed or network-isolated.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from contextlib import suppress
from pathlib import Path

import uvicorn
from alembic import command
from alembic.config import Config
from dotenv import load_dotenv

from evoagent.api.app import create_app
from evoagent.config import Settings
from evoagent.workers.bootstrap import run_maintenance_worker, run_worker

ROOT = Path(__file__).resolve().parents[1]
HOST_COMPOSE = ROOT / "deploy" / "host" / "compose.yml"


def configure() -> Settings:
    if os.name != "nt":
        raise RuntimeError("可信本机模式目前仅支持 Windows")
    private_config = ROOT / ".env.personal"
    if not private_config.is_file():
        raise RuntimeError("缺少 .env.personal；先按个人模式手册填写模型与预算")
    load_dotenv(private_config, override=True)
    os.environ.update(
        EVOAGENT_TRUSTED_HOST_MODE="true",
        EVOAGENT_DATABASE_URL=("postgresql+asyncpg://evoagent:evoagent@127.0.0.1:15436/evoagent"),
        EVOAGENT_REDIS_URL="redis://127.0.0.1:16382/0",
        EVOAGENT_REDIS_NAMESPACE="evoagent-host",
        EVOAGENT_API_HOST="127.0.0.1",
        EVOAGENT_API_PORT="18020",
        EVOAGENT_WORKER_ID="worker-windows-host",
        EVOAGENT_WORKSPACE=str(ROOT / "output" / "host-mode" / "workspace"),
        EVOAGENT_ARTIFACT_ROOT=str(ROOT / "output" / "host-mode" / "workspace" / "artifacts"),
        EVOAGENT_FRONTEND_DIST=str(ROOT / "frontend" / "dist"),
    )
    settings = Settings()
    if not settings.project_command_allowlist:
        raise RuntimeError("本机模式要求在 .env.personal 显式配置项目命令白名单")
    if settings.budget_scope != "trial" or settings.budget_trial_limit_micros is None:
        raise RuntimeError("本机模式的真实模型试跑需配置 trial 预算上限")
    return settings


def prepare_storage() -> None:
    subprocess.run(
        ["docker", "compose", "-p", "evoagent-host", "-f", str(HOST_COMPOSE), "up", "-d", "--wait"],
        cwd=ROOT,
        check=True,
    )
    configuration = Config(str(ROOT / "alembic.ini"))
    configuration.set_main_option("script_location", str(ROOT / "migrations"))
    command.upgrade(configuration, "head")


async def serve(settings: Settings) -> None:
    server = uvicorn.Server(
        uvicorn.Config(
            create_app(settings),
            host=settings.api_host,
            port=settings.api_port,
            log_level=settings.log_level.value.lower(),
        )
    )
    async with asyncio.TaskGroup() as group:
        group.create_task(server.serve(), name="host-api")
        group.create_task(run_worker(), name="host-worker")
        group.create_task(run_maintenance_worker(), name="host-maintenance")


def main() -> None:
    os.chdir(ROOT)
    settings = configure()
    prepare_storage()
    print(f"可信本机模式已启动：http://127.0.0.1:{settings.api_port}/ui/", flush=True)
    with suppress(KeyboardInterrupt):
        asyncio.run(serve(settings))


if __name__ == "__main__":
    sys.exit(main())
