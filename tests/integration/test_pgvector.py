"""真实 PostgreSQL 类型/算子验收；SQLite 不能替代此测试。"""

import os

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine


@pytest.mark.postgres
async def test_real_vector_dimension_cosine_and_generation_filter():
    url = os.getenv("EVOAGENT_TEST_DATABASE_URL")
    if not url:
        pytest.skip("EVOAGENT_TEST_DATABASE_URL is not configured")
    engine = create_async_engine(url)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            await conn.execute(
                text(
                    "CREATE TEMP TABLE vector_probe "
                    "(id int, generation int, embedding vector(1536))"
                )
            )
            import json

            first = json.dumps([1.0] + [0.0] * 1535)
            second = json.dumps([0.0, 1.0] + [0.0] * 1534)
            await conn.execute(
                text(
                    "INSERT INTO vector_probe VALUES (1,1,CAST(:a AS vector(1536))),"
                    "(2,1,CAST(:b AS vector(1536))),(3,2,CAST(:a AS vector(1536)))"
                ),
                {"a": first, "b": second},
            )
            rows = (
                await conn.execute(
                    text(
                        "SELECT id, embedding <=> CAST(:q AS vector(1536)) AS distance "
                        "FROM vector_probe WHERE generation=1 AND id IN (1,2) ORDER BY distance,id"
                    ),
                    {"q": first},
                )
            ).all()
            assert [row[0] for row in rows] == [1, 2]
            assert [row[1] for row in rows] == pytest.approx([0, 1])
            async with conn.begin_nested() as savepoint:
                with pytest.raises(DBAPIError):
                    await conn.execute(text("INSERT INTO vector_probe VALUES (4,1,'[1,2]')"))
                await savepoint.rollback()
    finally:
        await engine.dispose()
