"""每个连接由一个任务独占 SDK cancel scope，禁止跨任务开关 Session。"""

import asyncio
import logging
from contextlib import AsyncExitStack, suppress
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import mcp
from mcp import ClientSession, types
from mcp.shared.exceptions import McpError as SDKError

from evoagent.mcp.discovery import discover
from evoagent.mcp.schema import PROTOCOL_VERSION, MCPError
from evoagent.mcp.transports import open_transport
from evoagent.tasks.lease import LeaseLostError
from evoagent.tools.base import ToolExecutionError


class SDKDiagnosticFilter(logging.Filter):
    """SDK 的少量根日志调用同样不允许输出未经处理的远端报文。"""

    def filter(self, record):
        return not Path(record.pathname).is_relative_to(Path(mcp.__file__).parent)


def error_code(error):
    if isinstance(error, (MCPError, ToolExecutionError)):
        return error.code
    if isinstance(error, SDKError):
        return {-32000: "mcp_connection_closed", -32001: "mcp_timeout"}.get(
            error.error.code, "mcp_protocol_error"
        )
    if isinstance(error, TimeoutError):
        return "mcp_timeout"
    if isinstance(error, BaseExceptionGroup):
        codes = [error_code(child) for child in error.exceptions]
        return next((code for code in codes if code != "mcp_transport_failed"), codes[0])
    return "mcp_transport_failed"


class Connection:
    def __init__(self, config, settings, publish, observe, transport):
        self.config, self.settings = config, settings
        self.publish, self.observe, self.transport = publish, observe, transport
        self.wakeup = asyncio.Event()
        self.pending = []
        self.calls = []
        self.notification_epoch = 0
        self.state = "connecting"
        self.ready = asyncio.get_running_loop().create_future()
        self.task = asyncio.create_task(self.run())

    async def notify(self, message):
        if isinstance(message, types.ServerNotification) and isinstance(
            message.root, types.ToolListChangedNotification
        ):
            self.notification_epoch += 1
            self.wakeup.set()

    async def refresh(self):
        await asyncio.shield(self.ready)
        if self.task.done():
            raise MCPError("mcp_connection_closed")
        future = asyncio.get_running_loop().create_future()
        self.pending.append(future)
        self.wakeup.set()
        return await future

    async def call(self, operation):
        await asyncio.shield(self.ready)
        if self.task.done() or self.state != "ready":
            raise MCPError("mcp_connection_closed")
        future = asyncio.get_running_loop().create_future()
        self.calls.append((future, operation))
        self.wakeup.set()
        return await future

    async def scan(self, session, initialization):
        # 分页期间通知变化时整批重读，最多三次，避免混合两份目录。
        for _ in range(3):
            epoch = self.notification_epoch
            async with asyncio.timeout(self.config.call_timeout):
                tools = await discover(session)
            if epoch == self.notification_epoch:
                return await self.publish(tools, initialization)
        raise MCPError("mcp_catalog_unstable")

    async def run(self):
        try:
            await self.observe("connecting", None)
            async with AsyncExitStack() as stack:
                async with asyncio.timeout(self.config.connection_timeout):
                    streams = await stack.enter_async_context(
                        self.transport(self.config, self.settings)
                    )
                    session = await stack.enter_async_context(
                        ClientSession(
                            *streams,
                            read_timeout_seconds=timedelta(seconds=self.config.call_timeout),
                            message_handler=self.notify,
                            client_info=types.Implementation(
                                name="evoagent-discovery", version="0.4.0.dev0"
                            ),
                        )
                    )
                    initialization = await session.initialize()
                if initialization.protocolVersion != PROTOCOL_VERSION:
                    raise MCPError("mcp_protocol_unsupported")
                if initialization.capabilities.tools is None:
                    raise MCPError("mcp_tools_unsupported")
                catalog = await self.scan(session, initialization)
                self.state = "ready"
                await self.observe("ready", None)
                self.ready.set_result(catalog)
                while True:
                    try:
                        await asyncio.wait_for(self.wakeup.wait(), timeout=15)
                    except TimeoutError:
                        async with asyncio.timeout(self.config.call_timeout):
                            await session.send_ping()
                        await self.observe("ready", None)
                        continue
                    self.wakeup.clear()
                    if self.state == "draining":
                        return
                    waiting, self.pending = self.pending, []
                    try:
                        catalog = await self.scan(session, initialization)
                    except BaseException:
                        self.pending.extend(waiting)
                        raise
                    for future in waiting:
                        if not future.done():
                            future.set_result(catalog)
                    while self.calls:
                        future, operation = self.calls[0]
                        if not future.done():
                            try:
                                async with asyncio.timeout(self.config.call_timeout):
                                    result = await operation(session, catalog, future)
                                if not future.done():
                                    future.set_result(result)
                            except Exception as error:
                                if not future.done():
                                    future.set_exception(
                                        error
                                        if isinstance(error, LeaseLostError)
                                        else MCPError(error_code(error))
                                    )
                        self.calls.pop(0)
                        if self.state == "draining":
                            return
                    await self.observe("ready", None)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            # 不把 SDK ExceptionGroup、URL、报文或秘密写入健康记录。
            code = error_code(error)
            self.state = "degraded"
            with suppress(Exception):
                await self.observe("degraded", code)
            if not self.ready.done():
                self.ready.set_exception(MCPError(code))
        finally:
            if not self.ready.done():
                self.ready.set_exception(MCPError("mcp_connection_closed"))
            for future in self.pending + [item[0] for item in self.calls]:
                if not future.done():
                    future.set_exception(MCPError("mcp_connection_closed"))

    async def close(self):
        self.state = "draining"
        self.task.cancel()
        with suppress(asyncio.CancelledError):
            await self.task
        self.state = "disabled"
        # 取出未被等待的启动异常，避免关闭时产生未处理 Future 警告。
        if self.ready.done() and not self.ready.cancelled():
            self.ready.exception()


class ConnectionManager:
    def __init__(self, settings, transport=open_transport):
        self.settings, self.transport = settings, transport
        self.instance_id = f"mcp:{uuid4().hex}"
        self.connections = {}
        self.lock = asyncio.Lock()
        self.closed = False
        # SDK 可能记录任意远端报文；对外只暴露本模块的稳定错误码。
        logger = logging.getLogger("mcp")
        if not logger.handlers:
            logger.addHandler(logging.NullHandler())
        logger.propagate = False
        root = logging.getLogger()
        if not any(isinstance(item, SDKDiagnosticFilter) for item in root.filters):
            root.addFilter(SDKDiagnosticFilter())

    async def discover(self, identity, version, config, publish, observe):
        if not config.enabled:
            raise MCPError("mcp_server_disabled")
        # 模块 8 只做元数据发现；单进程串行建立连接，目录请求也有界。
        async with self.lock:
            if self.closed:
                raise MCPError("mcp_manager_closed")
            existing = self.connections.get(identity)
            if existing and (existing[0] != version or existing[1].task.done()):
                await existing[1].close()
                self.connections.pop(identity)
                existing = None
            if existing:
                return await existing[1].refresh()
            for key, (_, inactive) in tuple(self.connections.items()):
                if inactive.task.done():
                    await inactive.close()
                    self.connections.pop(key)
            if len(self.connections) >= self.settings.mcp_max_connections:
                raise MCPError("mcp_connection_limit")
            # 仅发现/握手可有限重试，绝不包含 tools/call。
            for attempt in range(2):
                connection = Connection(config, self.settings, publish, observe, self.transport)
                self.connections[identity] = (version, connection)
                try:
                    return await asyncio.shield(connection.ready)
                except MCPError as error:
                    await connection.close()
                    self.connections.pop(identity, None)
                    if attempt or error.code not in {"mcp_transport_failed", "mcp_timeout"}:
                        raise
                    await asyncio.sleep(0.1)
                except asyncio.CancelledError:
                    await connection.close()
                    self.connections.pop(identity, None)
                    raise

    async def drain(self, identity):
        async with self.lock:
            existing = self.connections.get(identity)
            if existing:
                connection = existing[1]
                connection.state = "draining"
                connection.wakeup.set()
                await asyncio.shield(connection.task)
                self.connections.pop(identity, None)
                await connection.observe("disabled", None)

    async def disconnect(self, identity):
        async with self.lock:
            existing = self.connections.pop(identity, None)
            if existing:
                await existing[1].close()
                await existing[1].observe("disabled", None)

    async def aclose(self):
        async with self.lock:
            self.closed = True
            for _, connection in self.connections.values():
                await connection.close()
                with suppress(Exception):
                    await connection.observe("disabled", None)
            self.connections.clear()
