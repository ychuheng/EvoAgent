import asyncio
import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import create_async_engine

EXPECTED_TABLES = {
    "validation_replica_bindings",
    "validation_judgments",
    "skill_trials",
    "skill_observations",
    "run_feedback",
    "learning_policies",
    "learning_request_aliases",
    "learning_requests",
    "learning_sources",
    "learning_spend_reservations",
    "runtime_experiments",
    "runtime_eval_runs",
    "sandbox_executions",
    "spend_records",
    "mcp_servers",
    "mcp_catalogs",
    "mcp_tool_reviews",
    "mcp_health",
    "embedding_profiles",
    "index_generations",
    "retrieval_documents",
    "document_embeddings",
    "retrieval_batches",
    "retrieval_selections",
    "workspaces",
    "projects",
    "project_events",
    "context_revisions",
    "memory_entries",
    "memory_versions",
    "memory_sources",
    "memory_events",
    "maintenance_jobs",
    "session_archives",
    "run_memory_references",
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


async def test_learning_migration_preserves_existing_skill_version_counter(tmp_path):
    url = f"sqlite+aiosqlite:///{tmp_path / 'learning-legacy.db'}"
    config = alembic_config(url)
    await asyncio.to_thread(command.upgrade, config, "20261009_0021")
    engine = create_async_engine(url)
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO skills "
                "(id,name,slug,description,status,lock_version,created_at,updated_at) "
                "VALUES (:id,'old','old','method','disabled',0,:at,:at)"
            ),
            {"id": "a" * 32, "at": "2026-01-01 00:00:00"},
        )
        await connection.execute(
            text(
                "INSERT INTO skill_versions "
                "(id,skill_id,version,schema_version,definition,content_hash,"
                "extraction_key,lifecycle_status,created_at) "
                "VALUES (:id,:skill,7,1,'{}',:hash,:hash,'draft',:at)"
            ),
            {
                "id": "b" * 32,
                "skill": "a" * 32,
                "hash": "sha256:" + "c" * 64,
                "at": "2026-01-01 00:00:00",
            },
        )
    await asyncio.to_thread(command.upgrade, config, "head")
    async with engine.connect() as connection:
        row = (
            await connection.execute(
                text("SELECT workspace_id,next_version_number FROM skills WHERE id=:id"),
                {"id": "a" * 32},
            )
        ).one()
        assert row[0] == "0" * 31 + "1"
        assert row[1] == 8
    await asyncio.to_thread(command.downgrade, config, "20261009_0021")
    async with engine.connect() as connection:
        assert await connection.scalar(text("SELECT version FROM skill_versions")) == 7
    await engine.dispose()


async def test_phase_four_migration_backfills_existing_messages(tmp_path):
    url = f"sqlite+aiosqlite:///{tmp_path / 'legacy.db'}"
    config = alembic_config(url)
    await asyncio.to_thread(command.upgrade, config, "20260919_0005")
    engine = create_async_engine(url)
    async with engine.begin() as connection:
        await connection.execute(
            text("INSERT INTO sessions (id,title,created_at) VALUES (:id,'old',:at)"),
            {"id": "1" * 32, "at": "2026-01-01 00:00:00"},
        )
        for index in range(2):
            await connection.execute(
                text(
                    "INSERT INTO messages (id,session_id,role,content,created_at) "
                    "VALUES (:id,:sid,'user',:body,:at)"
                ),
                {
                    "id": str(index + 2) * 32,
                    "sid": "1" * 32,
                    "body": f"old-{index}",
                    "at": "2026-01-01 00:00:00",
                },
            )
    await asyncio.to_thread(command.upgrade, config, "head")
    async with engine.connect() as connection:
        rows = (
            await connection.execute(
                text(
                    "SELECT session_sequence,kind,content_hash,backfill "
                    "FROM messages ORDER BY session_sequence"
                )
            )
        ).all()
        assert [row[0] for row in rows] == [1, 2]
        assert all(row[1] == "legacy" and row[2].startswith("sha256:") and row[3] for row in rows)
        scope = (
            await connection.execute(
                text("SELECT workspace_id,next_message_sequence FROM sessions")
            )
        ).one()
        assert scope[0] == "0" * 31 + "1" and scope[1] == 3
    await engine.dispose()


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


async def test_feedback_consent_migration_keeps_history_unconsented(tmp_path):
    from evoagent.db.models import RunRecord, SessionRecord, TaskRecord
    from evoagent.db.session import Database

    url = f"sqlite+aiosqlite:///{tmp_path / 'feedback-legacy.db'}"
    config = alembic_config(url)
    await asyncio.to_thread(command.upgrade, config, "20261009_0023")
    db = Database(url)
    async with db.session_factory() as session:
        chat = SessionRecord(title="old")
        session.add(chat)
        await session.flush()
        task = TaskRecord(session_id=chat.id, goal="old")
        session.add(task)
        await session.flush()
        run = RunRecord(
            task_id=task.id,
            provider="mock",
            model="mock",
            data_role="personal",
            next_feedback_revision=2,
        )
        session.add(run)
        await session.commit()
    async with db.engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO run_feedback "
                "(id,run_id,revision,learning_revision,learning_payload_hash,request_body_hash,intent,"
                "client_request_id,verdict,comment,correction,evidence_refs,actor_id,created_at) "
                "VALUES (:id,:run,1,1,:semantic,:body,'method','old','helpful',"
                "'note','method','[]','human',:at)"
            ),
            {
                "id": "f" * 32,
                "run": run.id.hex,
                "semantic": "sha256:" + "a" * 64,
                "body": "sha256:" + "b" * 64,
                "at": "2026-01-01 00:00:00",
            },
        )
    await asyncio.to_thread(command.upgrade, config, "head")
    async with db.engine.connect() as connection:
        row = (
            await connection.execute(
                text(
                    "SELECT learn_from_feedback,request_body_hash,comment,correction "
                    "FROM run_feedback"
                )
            )
        ).one()
        assert not row[0] and row[1:] == ("sha256:" + "b" * 64, "note", "method")
    await asyncio.to_thread(command.downgrade, config, "20261009_0023")
    async with db.engine.connect() as connection:
        columns = await connection.run_sync(lambda conn: inspect(conn).get_columns("run_feedback"))
        assert "learn_from_feedback" not in {column["name"] for column in columns}
        assert await connection.scalar(text("SELECT count(*) FROM run_feedback")) == 1
    await db.dispose()
