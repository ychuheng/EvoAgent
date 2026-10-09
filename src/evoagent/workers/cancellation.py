"""一进程一个 PostgreSQL 取消订阅；提示只唤醒受 fencing 保护的状态复查。"""

import asyncio
from contextlib import contextmanager
from uuid import UUID

import asyncpg

from evoagent.tasks.cancel_notifications import CHANNEL, TRIGGER_NAME


class CancellationNotifier:
    def __init__(self, session_factory, *, max_waiters: int = 1024, server_settings=None):
        if max_waiters < 1:
            raise ValueError("cancellation waiter capacity must be positive")
        self.engine = session_factory.kw.get("bind")
        self.max_waiters = max_waiters
        self.server_settings = server_settings or {"application_name": "evoagent-cancel-listener"}
        self.healthy = False
        self.unavailable_reason = "starting"
        self.disconnects = 0
        self._waiters: dict[UUID, set[asyncio.Event]] = {}
        self._closed = asyncio.Event()

    @contextmanager
    def register(self, task_id: UUID):
        if self._closed.is_set():
            raise RuntimeError("cancellation notifier is closed")
        if sum(len(values) for values in self._waiters.values()) >= self.max_waiters:
            raise ValueError("cancellation waiter capacity exceeded")
        signal = asyncio.Event()
        self._waiters.setdefault(task_id, set()).add(signal)
        try:
            yield signal
        finally:
            self._waiters[task_id].discard(signal)
            if not self._waiters[task_id]:
                del self._waiters[task_id]

    def _notify(self, _connection, _pid, _channel, payload):
        if len(payload) != 36:
            return
        try:
            task_id = UUID(payload)
        except ValueError:
            return
        for signal in self._waiters.get(task_id, ()):
            signal.set()

    def _degraded(self, reason):
        self.healthy = False
        self.unavailable_reason = reason
        # A lost subscription cannot silently hide a racing cancellation.
        for values in self._waiters.values():
            for signal in values:
                signal.set()

    async def _wait(self, stopping, *, disconnected=None, timeout=None):
        events = [stopping, self._closed]
        if disconnected is not None:
            events.append(disconnected)
        tasks = [asyncio.create_task(event.wait()) for event in events]
        try:
            await asyncio.wait(tasks, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def run(self, stopping: asyncio.Event):
        if self.engine is None or self.engine.dialect.name != "postgresql":
            self._degraded("not_postgresql")
            await self._wait(stopping)
            return
        delay = 1
        while not stopping.is_set() and not self._closed.is_set():
            connection = None
            disconnected = asyncio.Event()

            def terminated(_connection, signal=disconnected):
                self.disconnects += 1
                self._degraded("disconnected")
                signal.set()

            try:
                # Dedicated connection: no SQLAlchemy transaction is held while
                # idle, and it does not consume the task session pool.
                dsn = self.engine.url.set(drivername="postgresql").render_as_string(
                    hide_password=False
                )
                connection = await asyncpg.connect(
                    dsn, timeout=2, command_timeout=2, server_settings=self.server_settings
                )
                connection.add_termination_listener(terminated)
                await connection.add_listener(CHANNEL, self._notify)
                installed = await connection.fetchval(
                    "SELECT EXISTS(SELECT 1 FROM pg_trigger WHERE tgname=$1 "
                    "AND tgrelid=to_regclass('tasks') AND tgenabled='O' AND NOT tgisinternal)",
                    TRIGGER_NAME,
                )
                if installed:
                    self.healthy = True
                    self.unavailable_reason = None
                    delay = 1
                    await self._wait(stopping, disconnected=disconnected)
                else:
                    self._degraded("notification_trigger_missing")
            except (asyncpg.PostgresError, asyncpg.InterfaceError, OSError, TimeoutError):
                self._degraded("dependency_unavailable")
            finally:
                reason = "stopped" if stopping.is_set() else self.unavailable_reason
                self._degraded(reason or "disconnected")
                if connection is not None:
                    connection.remove_termination_listener(terminated)
                    try:
                        await connection.close(timeout=1)
                    except (asyncpg.PostgresError, asyncpg.InterfaceError, OSError, TimeoutError):
                        connection.terminate()
            if not stopping.is_set() and not self._closed.is_set():
                await self._wait(stopping, timeout=delay)
                delay = min(8, delay * 2)

    async def close(self):
        self._closed.set()
        self._degraded("closed")
