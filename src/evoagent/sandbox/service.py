"""执行记录、租约监控、限定输入/产物与崩溃清理。"""

import asyncio
import base64
import hashlib
import json
import shutil
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select

from evoagent.db.models import ArtifactRecord, SandboxExecutionRecord
from evoagent.sandbox.docker import SandboxError, bounded_output
from evoagent.sandbox.guest import MAX_FILE, MAX_FILES, MAX_TOTAL, safe_name
from evoagent.skills.canonical import content_hash
from evoagent.tasks.lease import JobLease
from evoagent.tasks.lease_guard import LeaseGuard
from evoagent.trace.artifacts import ArtifactService, LocalArtifactStore


def decode_outputs(payload):
    files = payload.get("files", [])
    if not isinstance(files, list) or len(files) > MAX_FILES:
        raise SandboxError("sandbox_file_limit")
    result, names, total = [], set(), 0
    for item in files:
        if not isinstance(item, dict) or set(item) != {"name", "data"}:
            raise SandboxError("sandbox_artifact_invalid")
        name = item["name"]
        if not isinstance(name, str) or not safe_name(name) or name in names:
            raise SandboxError("sandbox_artifact_path")
        names.add(name)
        try:
            body = base64.b64decode(item["data"], validate=True)
        except (ValueError, TypeError):
            raise SandboxError("sandbox_artifact_invalid") from None
        total += len(body)
        if len(body) > MAX_FILE or total > MAX_TOTAL:
            raise SandboxError("sandbox_artifact_size")
        result.append((name, body))
    return result


class SandboxService:
    def __init__(self, settings, factory, driver):
        self.settings, self.factory, self.driver = settings, factory, driver
        self.active = {}
        self.slots = asyncio.Semaphore(4)

    def profile(self, name, expected_hash=None, mode="job"):
        spec = self.settings.sandbox_profiles.get(name)
        if spec is None or spec.mode != mode:
            raise SandboxError("sandbox_profile_unavailable")
        if (
            expected_hash is not None
            and content_hash(spec.model_dump(mode="json")) != expected_hash
        ):
            raise SandboxError("sandbox_profile_changed")
        if mode == "stdio" and not spec.stdio_command:
            raise SandboxError("sandbox_profile_unavailable")
        return spec

    @staticmethod
    def guard(request):
        return LeaseGuard(
            JobLease(
                task_id=request.task_id,
                run_id=request.run_id,
                owner=request.owner,
                epoch=request.epoch,
                expires_at=datetime.now(UTC),
                attempt=0,
            )
        )

    async def check(self, guard):
        async with self.factory() as session:
            task, _ = await guard.check(session)
            if task.cancel_requested:
                raise SandboxError("sandbox_cancelled")

    async def record(self, identity, spec, *, run_id=None, epoch=None, server_id=None):
        async with self.factory() as session:
            if await session.get(SandboxExecutionRecord, identity):
                raise SandboxError("sandbox_execution_exists")
            session.add(
                SandboxExecutionRecord(
                    id=identity,
                    run_id=run_id,
                    lease_epoch=epoch,
                    server_id=server_id,
                    profile_hash=content_hash(spec.model_dump(mode="json")),
                    expires_at=datetime.now(UTC) + timedelta(seconds=spec.timeout_seconds),
                )
            )
            await session.commit()

    async def update(self, identity, **values):
        operation = asyncio.create_task(self._update(identity, **values))
        try:
            await asyncio.shield(operation)
        except asyncio.CancelledError:
            # 等待短事务结束再关闭容器，避免取消 commit 留下未释放的连接/锁。
            await operation
            raise

    async def _update(self, identity, **values):
        # 可信控制器只更新执行证据，不提交 Task/Effect 业务状态。
        async with self.factory() as session:
            row = await session.get(SandboxExecutionRecord, identity)
            for key, value in values.items():
                setattr(row, key, value)
            await session.commit()

    async def stage(self, request, guard):
        root = self.settings.sandbox_staging_root.resolve()
        path = root / str(request.run_id) / str(request.epoch) / str(request.execution_id) / "input"
        path.mkdir(parents=True, exist_ok=False)
        path.chmod(0o755)
        total = 0
        async with self.factory() as session:
            await guard.check(session)
            for identity in request.input_artifact_ids:
                artifact = await session.get(ArtifactRecord, identity)
                if (
                    artifact is None
                    or artifact.run_id != request.run_id
                    or artifact.size_bytes > MAX_FILE
                ):
                    raise SandboxError("sandbox_input_forbidden")
                store_root = self.settings.artifact_root.resolve()
                source = (store_root / artifact.uri).resolve(strict=True)
                if not source.is_relative_to(store_root) or not source.is_file():
                    raise SandboxError("sandbox_input_forbidden")
                with source.open("rb") as stream:
                    body = stream.read(MAX_FILE + 1)
                total += len(body)
                digest = "sha256:" + hashlib.sha256(body).hexdigest()
                if len(body) > MAX_FILE or total > MAX_TOTAL or digest != artifact.content_hash:
                    raise SandboxError("sandbox_input_invalid")
                target = path / str(identity)
                target.write_bytes(body)
                target.chmod(0o444)
        return path

    def erase_stage(self, request):
        root = self.settings.sandbox_staging_root.resolve()
        path = (
            root / str(request.run_id) / str(request.epoch) / str(request.execution_id)
        ).resolve()
        if path != root and path.is_relative_to(root) and path.exists():
            for child in path.rglob("*"):
                if child.is_file() and child.resolve().is_relative_to(path):
                    child.chmod(0o600)
            shutil.rmtree(path)

    async def cleanup(self, identity, status, code=None):
        try:
            await self.driver.remove(f"evoagent-sandbox-{identity}")
        except Exception:
            await self.update(
                identity, status="cleanup_pending", error_code="sandbox_cleanup_failed"
            )
            raise SandboxError("sandbox_cleanup_failed") from None
        await self.update(identity, status=status, error_code=code, finished_at=datetime.now(UTC))

    async def run(self, request, disconnected=None):
        spec = self.profile(request.profile, request.profile_hash)
        if request.argv[0] not in spec.allowed_executables:
            raise SandboxError("sandbox_executable_forbidden")
        guard = self.guard(request)
        await self.check(guard)
        async with self.slots:
            await self.check(guard)
            await self.record(
                request.execution_id, spec, run_id=request.run_id, epoch=request.epoch
            )
            self.active[request.execution_id] = asyncio.current_task()
            process = reader = None
            status, code = "failed", "sandbox_failed"
            cleaned = False
            try:
                async with asyncio.timeout(spec.timeout_seconds):
                    stage = await self.stage(request, guard)
                    name = f"evoagent-sandbox-{request.execution_id}"
                    container = await self.driver.create(name, spec, stage, request.argv)
                    await self.update(
                        request.execution_id, container_id=container, status="running"
                    )
                    await self.check(guard)
                    process = await self.driver.start(name)
                    reader = asyncio.create_task(bounded_output(process, 16 * 1048576))
                    while not reader.done():
                        await asyncio.wait({reader}, timeout=0.25)
                        await self.check(guard)
                        if disconnected is not None and await disconnected():
                            raise SandboxError("sandbox_client_disconnected")
                    stdout, _ = await reader
                    if process.returncode:
                        raise SandboxError("sandbox_container_failed")
                    payload = json.loads(stdout)
                    if not isinstance(payload, dict) or "error_code" in payload:
                        raise SandboxError("sandbox_guest_failed")
                    if (
                        not isinstance(payload.get("return_code"), int)
                        or not isinstance(payload.get("stdout"), str)
                        or not isinstance(payload.get("stderr"), str)
                        or len((payload["stdout"] + payload["stderr"]).encode()) > spec.output_bytes
                    ):
                        raise SandboxError("sandbox_output_invalid")
                    files = decode_outputs(payload)
                    # 先确认整个容器停止/删除，再发布带 epoch 的产物。
                    await self.cleanup(request.execution_id, "stopped")
                    cleaned = True
                    await self.check(guard)
                    artifacts = []
                    publisher = ArtifactService(
                        LocalArtifactStore(self.settings.artifact_root),
                        self.factory,
                        lease_guard=guard,
                    )
                    for original, body in files:
                        digest = hashlib.sha256(body).hexdigest()
                        row = await publisher.create_unique(
                            run_id=request.run_id,
                            name=f"sandbox-{request.epoch}-{request.execution_id}-{len(artifacts)}-{digest}",
                            content=body,
                            artifact_type="sandbox_output",
                            attributes={
                                "execution_id": str(request.execution_id),
                                "lease_epoch": request.epoch,
                                "original_name": original,
                            },
                        )
                        artifacts.append(
                            {"id": str(row.id), "name": original, "content_hash": row.content_hash}
                        )
                    status, code = (
                        ("succeeded", None)
                        if payload["return_code"] == 0
                        else ("failed", "sandbox_command_failed")
                    )
                    return {
                        "return_code": payload["return_code"],
                        "stdout": payload["stdout"],
                        "stderr": payload["stderr"],
                        "artifacts": artifacts,
                    }
            except TimeoutError:
                code = "sandbox_timeout"
                raise SandboxError(code) from None
            except asyncio.CancelledError:
                status, code = "cancelled", "sandbox_cancelled"
                raise
            except Exception as error:
                code = getattr(error, "code", "sandbox_failed")
                raise SandboxError(code) from None
            finally:
                if reader is not None:
                    reader.cancel()
                    with suppress(asyncio.CancelledError, Exception):
                        await reader
                try:
                    if not cleaned:
                        await asyncio.shield(self.cleanup(request.execution_id, status, code))
                    else:
                        await self.update(request.execution_id, status=status, error_code=code)
                    self.erase_stage(request)
                finally:
                    self.active.pop(request.execution_id, None)

    async def reap(self):
        # 单实例 controller；进程不再持有的所有遗留执行均保守停止，不重放。
        async with self.factory() as session:
            rows = list(
                await session.scalars(
                    select(SandboxExecutionRecord).where(
                        SandboxExecutionRecord.status.in_(
                            ["starting", "running", "stopped", "cleanup_pending"]
                        )
                    )
                )
            )
        for row in rows:
            if row.id not in self.active:
                with suppress(Exception):
                    await self.cleanup(row.id, "reaped", "sandbox_controller_restarted")
                    if row.run_id is not None:
                        from types import SimpleNamespace

                        self.erase_stage(
                            SimpleNamespace(
                                run_id=row.run_id, epoch=row.lease_epoch, execution_id=row.id
                            )
                        )
        for name in await self.driver.managed():
            try:
                identity = UUID(name.removeprefix("evoagent-sandbox-"))
            except ValueError:
                continue
            if identity not in self.active:
                await self.driver.remove(name)
