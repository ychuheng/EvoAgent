"""异步数据库引擎、会话工厂与资源释放。"""

from typing import Self

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import Session


@event.listens_for(Session, "after_flush")
def _queue_changed(session, context):
    from evoagent.db.models import MaintenanceJobRecord, TaskRecord

    for row in session.new | session.dirty:
        if (isinstance(row, TaskRecord) and row.status == "queued") or (
            isinstance(row, MaintenanceJobRecord) and row.status == "pending"
        ):
            session.info["queue_changed"] = True


class QueueSession(AsyncSession):
    async def commit(self):
        await super().commit()
        changed = self.info.pop("queue_changed", False)
        wakeup = self.info.get("wakeup")
        if changed and wakeup is not None:
            await wakeup.publish()

    async def rollback(self):
        await super().rollback()
        self.info.pop("queue_changed", None)


class Database:
    """集中拥有数据库引擎，并为每个工作单元创建独立会话。"""

    def __init__(self, url: str, *, echo: bool = False) -> None:
        self.engine: AsyncEngine = create_async_engine(url, echo=echo, pool_pre_ping=True)
        self.session_factory = async_sessionmaker(
            self.engine,
            class_=QueueSession,
            expire_on_commit=False,
        )

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.dispose()

    async def dispose(self) -> None:
        """关闭连接池拥有的全部数据库连接。"""

        await self.engine.dispose()
