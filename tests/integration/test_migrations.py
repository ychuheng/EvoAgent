import asyncio
import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import create_async_engine

EXPECTED_TABLES = {
    "alembic_version",
    "artifacts",
    "messages",
    "run_events",
    "run_snapshots",
    "runs",
    "sessions",
    "tasks",
    "tool_approvals",
    "tool_calls",
    "tool_effects",
    "turns",
    "skills",
    "skill_versions",
    "skill_sources",
    "skill_events",
    "eval_datasets",
    "eval_cases",
    "eval_experiments",
    "eval_runs",
    "run_skill_selections",
    "promotion_decisions",
}


def alembic_config(database_url: str) -> Config:
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return config


async def table_names(database_url: str) -> set[str]:
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            return await connection.run_sync(
                lambda sync_connection: set(inspect(sync_connection).get_table_names())
            )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_initial_migration_upgrades_and_downgrades_sqlite(tmp_path: Path) -> None:
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'migration.db'}"
    config = alembic_config(database_url)

    await asyncio.to_thread(command.upgrade, config, "head")
    assert await table_names(database_url) == EXPECTED_TABLES
    await asyncio.to_thread(command.check, config)

    await asyncio.to_thread(command.downgrade, config, "base")
    assert await table_names(database_url) == {"alembic_version"}


@pytest.mark.asyncio
@pytest.mark.postgres
async def test_initial_migration_on_real_postgresql() -> None:
    database_url = os.getenv("EVOAGENT_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("EVOAGENT_TEST_DATABASE_URL is not configured")
    config = alembic_config(database_url)

    await asyncio.to_thread(command.downgrade, config, "base")
    await asyncio.to_thread(command.upgrade, config, "head")
    assert await table_names(database_url) == EXPECTED_TABLES
    await asyncio.to_thread(command.check, config)
    await asyncio.to_thread(command.downgrade, config, "base")
