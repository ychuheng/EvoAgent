"""在两个独立空验收数据库验证 fresh 和带 v0.3 消息的升级；拒绝已有表。"""

import asyncio
import json
import os
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text

from evoagent.db.session import Database


async def verify(url, *, legacy):
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    async with Database(url) as db:
        async with db.engine.connect() as connection:
            tables = await connection.run_sync(lambda c: inspect(c).get_table_names())
            if tables:
                raise ValueError("acceptance migration requires an empty database")
        if legacy:
            await asyncio.to_thread(command.upgrade, config, "20260919_0005")
            async with db.engine.begin() as connection:
                await connection.execute(
                    text(
                        "INSERT INTO sessions (id,title,created_at) VALUES "
                        "('11111111-1111-1111-1111-111111111111','legacy',CURRENT_TIMESTAMP)"
                    )
                )
                for number in (2, 3):
                    await connection.execute(
                        text(
                            "INSERT INTO messages (id,session_id,role,content,created_at) "
                            "VALUES (:id,'11111111-1111-1111-1111-111111111111',"
                            "'user',:body,CURRENT_TIMESTAMP)"
                        ),
                        {"id": str(number) * 32, "body": f"legacy-{number}"},
                    )
        await asyncio.to_thread(command.upgrade, config, "head")
        await asyncio.to_thread(command.check, config)
        async with db.engine.connect() as connection:
            revision = await connection.scalar(text("SELECT version_num FROM alembic_version"))
            extension = await connection.scalar(
                text("SELECT extversion FROM pg_extension WHERE extname='vector'")
            )
            rows = (
                await connection.execute(
                    text(
                        "SELECT session_sequence,content,kind,backfill,content_hash "
                        "FROM messages ORDER BY session_sequence"
                    )
                )
            ).all()
        if legacy:
            assert [r[0] for r in rows] == [1, 2]
            assert {r[1] for r in rows} == {"legacy-2", "legacy-3"}
            assert all(r[2] == "legacy" and r[3] and r[4].startswith("sha256:") for r in rows)
        assert revision == "20260922_0012" and extension
        return {
            "head": revision,
            "vector_version": extension,
            "schema_drift": False,
            "legacy_messages_preserved": len(rows),
            "passed": True,
        }


async def main():
    report = {}
    for name in ("fresh", "legacy"):
        report[name] = await verify(
            os.environ[f"EVOAGENT_{name.upper()}_DATABASE_URL"], legacy=name == "legacy"
        )
    Path("docs/reports/phase4-final-migrations.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    asyncio.run(main())
