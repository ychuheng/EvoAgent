"""支持异步数据库驱动的 Alembic 运行环境。"""

import asyncio
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from evoagent.db import models  # noqa: F401
from evoagent.db.base import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

database_url = os.getenv("EVOAGENT_DATABASE_URL") or config.get_main_option("sqlalchemy.url")
config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """在不创建连接时生成 SQL。"""

    context.configure(
        url=database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection) -> None:
    """在 Alembic 提供的同步连接包装中执行迁移。"""

    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """创建异步引擎，并把迁移交给同步 Alembic 上下文。"""

    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_async_migrations())
