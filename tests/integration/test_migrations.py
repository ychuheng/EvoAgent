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
        # Core INSERT omits later nullable columns (e.g. family) when seeding
        # a genuinely old schema; the current ORM would explicitly send NULL.
        task_id = await session.scalar(
            TaskRecord.__table__.insert()
            .values(session_id=chat.id, goal="old")
            .returning(TaskRecord.id)
        )
        run = RunRecord(
            task_id=task_id,
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


async def test_task_family_upgrade_preserves_unknown_history_and_blocks_loss(tmp_path):
    from evoagent.db.models import SessionRecord, TaskRecord
    from evoagent.db.session import Database

    url = f"sqlite+aiosqlite:///{tmp_path / 'family-legacy.db'}"
    config = alembic_config(url)
    await asyncio.to_thread(command.upgrade, config, "20261010_0030")
    db = Database(url)
    async with db.session_factory() as session:
        chat = SessionRecord(title="old data task")
        session.add(chat)
        await session.flush()
        await session.execute(TaskRecord.__table__.insert().values(session_id=chat.id, goal="data"))
        await session.commit()
    await asyncio.to_thread(command.upgrade, config, "head")
    async with db.engine.begin() as connection:
        assert await connection.scalar(text("SELECT family FROM tasks")) is None
        await connection.execute(text("UPDATE tasks SET family='data'"))
    with pytest.raises(RuntimeError, match="frozen task family evidence"):
        await asyncio.to_thread(command.downgrade, config, "20261010_0030")
    async with db.engine.begin() as connection:
        assert await connection.scalar(text("SELECT family FROM tasks")) == "data"
        # Isolated migration fixture only: remove this evidence to test rollback.
        await connection.execute(text("UPDATE tasks SET family=NULL"))
    await asyncio.to_thread(command.downgrade, config, "20261010_0030")
    await asyncio.to_thread(command.upgrade, config, "head")
    await asyncio.to_thread(command.check, config)
    await db.dispose()


async def test_selection_evidence_migration_keeps_legacy_and_refuses_loss(tmp_path):
    from evoagent.db.models import (
        RunRecord,
        RunSkillSelectionRecord,
        SessionRecord,
        SkillRecord,
        SkillVersionRecord,
        TaskRecord,
    )
    from evoagent.db.session import Database

    url = f"sqlite+aiosqlite:///{tmp_path / 'selection-evidence.db'}"
    config = alembic_config(url)
    await asyncio.to_thread(command.upgrade, config, "20261010_0031")
    db = Database(url)
    digest = "sha256:" + "a" * 64
    async with db.session_factory() as session:
        chat = SessionRecord(title="legacy")
        skill = SkillRecord(slug="legacy", name="legacy", description="fixture")
        session.add_all([chat, skill])
        await session.flush()
        task_id = await session.scalar(
            TaskRecord.__table__.insert()
            .values(session_id=chat.id, goal="legacy")
            .returning(TaskRecord.id)
        )
        version = SkillVersionRecord(
            skill_id=skill.id,
            version=1,
            schema_version=1,
            definition={"fixture": True},
            content_hash=digest,
            extraction_key=digest,
        )
        session.add(version)
        await session.flush()
        run = RunRecord(task_id=task_id, provider="mock", model="mock")
        session.add(run)
        await session.flush()
        for rank in [1, 2]:
            await session.execute(
                RunSkillSelectionRecord.__table__.insert().values(
                    run_id=run.id,
                    skill_version_id=version.id,
                    mode="retrieval",
                    rank=rank,
                    score=1,
                    query_terms=[],
                )
            )
        await session.commit()
    await asyncio.to_thread(command.upgrade, config, "head")
    async with db.engine.begin() as connection:
        rows = (
            await connection.execute(
                text(
                    "SELECT rank,origin,content_hash,applicability,selection_policy_version "
                    "FROM run_skill_selections ORDER BY rank"
                )
            )
        ).all()
        assert rows == [(1, "legacy", None, None, None), (2, "legacy", None, None, None)]
        # Isolated synthetic fixture: prove downgrade protection, not a production retrofit.
        await connection.execute(
            text(
                "UPDATE run_skill_selections SET origin='formal',scope_key='fixture', "
                "content_hash=:hash,rendered_hash=:hash,applicability='{}', "
                "selection_policy_version='skill-selector-v1' WHERE rank=1"
            ),
            {"hash": digest},
        )
    with pytest.raises(RuntimeError, match="frozen v3 selection evidence"):
        await asyncio.to_thread(command.downgrade, config, "20261010_0031")
    async with db.engine.begin() as connection:
        assert (
            await connection.scalar(
                text("SELECT content_hash FROM run_skill_selections WHERE rank=1")
            )
            == digest
        )
        # Explicitly remove only the isolated synthetic evidence to exercise safe rollback.
        await connection.execute(
            text(
                "UPDATE run_skill_selections SET content_hash=NULL,applicability=NULL, "
                "selection_policy_version=NULL WHERE rank=1"
            )
        )
    await asyncio.to_thread(command.downgrade, config, "20261010_0031")
    await asyncio.to_thread(command.upgrade, config, "head")
    await asyncio.to_thread(command.check, config)
    await db.dispose()


async def test_task_selection_contract_migration_preserves_queued_history(tmp_path):
    from evoagent.db.models import SessionRecord, TaskRecord
    from evoagent.db.session import Database

    url = f"sqlite+aiosqlite:///{tmp_path / 'selection-routing.db'}"
    config = alembic_config(url)
    await asyncio.to_thread(command.upgrade, config, "20261010_0032")
    db = Database(url)
    async with db.session_factory() as session:
        chat = SessionRecord(title="old queued")
        session.add(chat)
        await session.flush()
        task_id = await session.scalar(
            TaskRecord.__table__.insert()
            .values(session_id=chat.id, goal="old queued", status="queued")
            .returning(TaskRecord.id)
        )
        await session.commit()
    await asyncio.to_thread(command.upgrade, config, "head")
    async with db.session_factory() as session:
        task = await session.get(TaskRecord, task_id)
        assert task.selection_contract_version is None and str(task.status) == "queued"
        task.selection_contract_version = 3
        with pytest.raises(ValueError, match="immutable"):
            await session.commit()
        await session.rollback()
        # Synthetic routing fixture only, no production task is retrofitted.
        await session.execute(
            TaskRecord.__table__.update()
            .where(TaskRecord.id == task_id)
            .values(selection_contract_version=3)
        )
        await session.commit()
    with pytest.raises(RuntimeError, match="tasks require v3 selection routing"):
        await asyncio.to_thread(command.downgrade, config, "20261010_0032")
    async with db.engine.begin() as connection:
        await connection.execute(text("UPDATE tasks SET selection_contract_version=NULL"))
    await asyncio.to_thread(command.downgrade, config, "20261010_0032")
    await asyncio.to_thread(command.upgrade, config, "head")
    await asyncio.to_thread(command.check, config)
    await db.dispose()
