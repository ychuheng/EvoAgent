"""独立可信控制器；认证、固定规格、生命周期清理，不对公网发布。"""

import asyncio
import hmac
import json
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi import FastAPI, HTTPException, Request, WebSocket
from fastapi.responses import JSONResponse

from evoagent.config import Settings
from evoagent.db.models import MCPServerRecord
from evoagent.db.session import Database
from evoagent.sandbox.docker import DockerDriver, SandboxError
from evoagent.sandbox.ownership import ControllerLock
from evoagent.sandbox.schema import SandboxRequest
from evoagent.sandbox.service import SandboxService
from evoagent.skills.canonical import content_hash
from evoagent.tasks.lease import JobLease
from evoagent.tasks.lease_guard import LeaseGuard


def authorized(settings, headers):
    token = settings.sandbox_controller_token
    expected = token.get_secret_value() if token else ""
    return len(expected) >= 32 and hmac.compare_digest(
        headers.get("authorization", ""), "Bearer " + expected
    )


async def stdio_session(channel, service):
    identity, tasks, process = uuid4(), [], None
    created = False
    status, code = "failed", "sandbox_stdio_failed"
    try:
        raw = await asyncio.wait_for(channel.receive_text(), 10)
        if len(raw.encode()) > 4096:
            raise SandboxError("sandbox_request_limit")
        request = json.loads(raw)
        if set(request) - {"server_id", "config_version", "profile_hash", "lease"}:
            raise SandboxError("sandbox_request_invalid")
        server_id = UUID(request["server_id"])
        async with service.factory() as session:
            server = await session.get(MCPServerRecord, server_id)
            if (
                server is None
                or not server.config.get("enabled")
                or server.lock_version != request["config_version"]
            ):
                raise SandboxError("sandbox_server_disabled")
            launch = service.settings.mcp_launch_profiles.get(
                server.config.get("launch_profile_id")
            )
            if launch is None or not launch.sandbox_profile_id or server.config.get("secret_ref"):
                raise SandboxError("sandbox_profile_unavailable")
            spec = service.profile(launch.sandbox_profile_id, request["profile_hash"], "stdio")
        guard = None
        if request.get("lease"):
            data = request["lease"]
            guard = LeaseGuard(
                JobLease(
                    task_id=UUID(data["task_id"]),
                    run_id=UUID(data["run_id"]),
                    owner=data["owner"],
                    epoch=int(data["epoch"]),
                    expires_at=datetime.now(UTC),
                    attempt=0,
                )
            )
            await service.check(guard)
        await service.record(
            identity,
            spec,
            server_id=server_id,
            run_id=guard.lease.run_id if guard else None,
            epoch=guard.lease.epoch if guard else None,
        )
        created = True
        service.active[identity] = asyncio.current_task()
        async with asyncio.timeout(spec.timeout_seconds):
            name = f"evoagent-sandbox-{identity}"
            container = await service.driver.create(name, spec, None, (), stdio=True)
            await service.update(identity, container_id=container, status="running")
            process = await service.driver.start(name, stdin=True)
            output_size, input_size = 0, 0

            async def from_container():
                nonlocal output_size
                # create_subprocess_exec 默认 readline 上限 64KiB；超长单消息拒绝。
                while line := await process.stdout.readline():
                    output_size += len(line)
                    if output_size > spec.output_bytes:
                        raise SandboxError("sandbox_output_limit")
                    await channel.send_text(line.decode("utf-8").rstrip("\n"))

            async def to_container():
                nonlocal input_size
                while True:
                    line = await channel.receive_text()
                    if "\n" in line or "\r" in line:
                        raise SandboxError("sandbox_stdio_invalid")
                    body = (line + "\n").encode()
                    input_size += len(body)
                    if input_size > 4194304 or len(body) > 65536:
                        raise SandboxError("sandbox_input_limit")
                    process.stdin.write(body)
                    await process.stdin.drain()

            async def stderr():
                nonlocal output_size
                while chunk := await process.stderr.read(4096):
                    output_size += len(chunk)
                    if output_size > spec.output_bytes:
                        raise SandboxError("sandbox_output_limit")

            tasks = [
                asyncio.create_task(from_container()),
                asyncio.create_task(to_container()),
                asyncio.create_task(stderr()),
            ]
            while True:
                done, _ = await asyncio.wait(
                    tasks, timeout=0.25, return_when=asyncio.FIRST_COMPLETED
                )
                if done:
                    for task in done:
                        task.result()
                    break
                if guard:
                    await service.check(guard)
                async with service.factory() as session:
                    current = await session.get(MCPServerRecord, server_id)
                    if (
                        current is None
                        or not current.config.get("enabled")
                        or current.lock_version != request["config_version"]
                    ):
                        raise SandboxError("sandbox_server_disabled")
            status, code = "stopped", None
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if process and process.returncode is None:
            with suppress(ProcessLookupError):
                process.kill()

            async def discard(stream):
                while await stream.read(65536):
                    pass

            with suppress(TimeoutError):
                async with asyncio.timeout(3):
                    await asyncio.gather(
                        discard(process.stdout), discard(process.stderr), process.wait()
                    )
        if created:
            try:
                await asyncio.shield(service.cleanup(identity, status, code))
            finally:
                service.active.pop(identity, None)


def create_controller(settings, database, driver=None):
    service = SandboxService(settings, database.session_factory, driver or DockerDriver())

    async def sweep():
        while True:
            with suppress(Exception):
                await service.reap()
            await asyncio.sleep(5)

    @asynccontextmanager
    async def lifespan(app):
        if not authorized(
            settings,
            {
                "authorization": "Bearer "
                + (
                    settings.sandbox_controller_token.get_secret_value()
                    if settings.sandbox_controller_token
                    else ""
                )
            },
        ):
            raise RuntimeError("controller requires a token of at least 32 characters")
        ownership = ControllerLock(settings.sandbox_staging_root.resolve())
        ownership.acquire()
        try:
            await service.reap()  # Docker 不可达时启动失败，不接受执行后再退回宿主。
        except BaseException:
            ownership.close()
            raise
        sweeper = asyncio.create_task(sweep())
        try:
            yield
        finally:
            sweeper.cancel()
            with suppress(asyncio.CancelledError):
                await sweeper
            tasks = list(service.active.values())
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            ownership.close()

    app = FastAPI(lifespan=lifespan)
    app.state.sandbox = service

    @app.middleware("http")
    async def auth(request: Request, call_next):
        if not authorized(settings, request.headers):
            return JSONResponse({"error_code": "sandbox_unauthorized"}, status_code=401)
        return await call_next(request)

    @app.exception_handler(SandboxError)
    async def failure(request, error):
        return JSONResponse({"error_code": error.code}, status_code=409)

    @app.post("/executions")
    async def execute(body: SandboxRequest, request: Request):
        return await service.run(body, request.is_disconnected)

    @app.delete("/executions/{identity}", status_code=204)
    async def cancel(identity: UUID):
        task = service.active.get(identity)
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    @app.get("/profiles/{name}")
    async def profile(name: str):
        spec = settings.sandbox_profiles.get(name)
        if spec is None:
            raise HTTPException(404)
        return {"hash": content_hash(spec.model_dump(mode="json"))}

    @app.websocket("/stdio")
    async def stdio(channel: WebSocket):
        if not authorized(settings, channel.headers):
            await channel.close(code=1008)
            return
        await channel.accept()
        try:
            async with service.slots:
                await stdio_session(channel, service)
        except Exception:
            pass  # 不把 SDK/CLI 输出或秘密回显到日志。
        finally:
            with suppress(Exception):
                await channel.close()

    return app


def main():
    import uvicorn

    settings = Settings()
    database = Database(settings.database_url.get_secret_value())
    uvicorn.run(
        create_controller(settings, database),
        host="0.0.0.0",
        port=8090,
        ws_max_size=65536,
        ws_max_queue=8,
        access_log=False,
    )


if __name__ == "__main__":
    main()
