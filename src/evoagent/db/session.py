"""异步数据库引擎、会话工厂与资源释放。"""

from contextlib import asynccontextmanager
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
    from evoagent.db.models import MaintenanceJobRecord, RunEventRecord, RunRecord, TaskRecord

    for row in session.new | session.dirty:
        if (isinstance(row, TaskRecord) and row.status == "queued") or (
            isinstance(row, MaintenanceJobRecord) and row.status == "pending"
        ):
            session.info["queue_changed"] = True
        if isinstance(row, (RunEventRecord, RunRecord)) and "event_notifier" in session.info:
            transaction = session.get_nested_transaction() or session.get_transaction()
            pending = session.info.setdefault("event_notifications", {})
            pending.setdefault(transaction, set()).add(
                row.run_id if isinstance(row, RunEventRecord) else row.id
            )


@event.listens_for(Session, "after_commit")
def _events_committed(session):
    transaction = session.get_nested_transaction() or session.get_transaction()
    pending = session.info.get("event_notifications", {})
    run_ids = pending.pop(transaction, set())
    if transaction is not None and transaction.nested:
        pending.setdefault(transaction.parent, set()).update(run_ids)
        return
    notifier = session.info.get("event_notifier")
    if notifier is not None and run_ids:
        notifier.schedule_publish(run_ids)


@event.listens_for(Session, "after_soft_rollback")
def _events_rolled_back(session, previous_transaction):
    session.info.get("event_notifications", {}).pop(previous_transaction, None)


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

    @classmethod
    @asynccontextmanager
    async def configured(cls, settings):
        """独立事件生产者复用相同提交后 helper，并拥有其通知资源。"""
        from evoagent.trace.notifications import RunEventNotifier
        from evoagent.workers.wakeup import redis_client

        client = redis_client(settings) if settings.runtime_shared_notifications_enabled else None
        notifier = (
            RunEventNotifier(client, settings.redis_namespace)
            if settings.runtime_shared_notifications_enabled
            else None
        )
        try:
            async with cls(settings.database_url.get_secret_value()) as database:
                if notifier is not None:
                    database.session_factory.configure(info={"event_notifier": notifier})
                yield database
        finally:
            if notifier is not None:
                await notifier.close()
            if client is not None:
                await client.aclose()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.dispose()

    async def dispose(self) -> None:
        """关闭连接池拥有的全部数据库连接。"""

        await self.engine.dispose()
