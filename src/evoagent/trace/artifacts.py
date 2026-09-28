"""Artifact 二进制存储与数据库元数据登记。"""

import asyncio
import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from evoagent.db.models import ArtifactRecord
from evoagent.db.unit_of_work import UnitOfWork
from evoagent.memory.repository import check_run_references
from evoagent.tasks.lease_guard import LeaseGuard


@dataclass(frozen=True, slots=True)
class StoredArtifact:
    uri: str
    content_hash: str
    size_bytes: int


class ArtifactStore(Protocol):
    async def write(self, run_id: UUID, name: str, content: bytes) -> StoredArtifact: ...

    async def read(self, uri: str) -> bytes: ...

    async def write_unique(self, run_id: UUID, name: str, content: bytes) -> StoredArtifact: ...


class LocalArtifactStore:
    """在受控根目录内原子写入 Artifact，拒绝路径穿越。"""

    def __init__(self, root: Path) -> None:
        self._root = root.expanduser().resolve(strict=False)
        self._root.mkdir(parents=True, exist_ok=True)

    async def write(self, run_id: UUID, name: str, content: bytes) -> StoredArtifact:
        return await asyncio.to_thread(self._write, run_id, name, content)

    async def write_unique(self, run_id: UUID, name: str, content: bytes) -> StoredArtifact:
        """以排他创建方式写入，保证并发请求也不能覆盖同名文件。"""

        return await asyncio.to_thread(self._write_unique, run_id, name, content)

    def _write(self, run_id: UUID, name: str, content: bytes) -> StoredArtifact:
        normalized_name = self._safe_name(name)
        run_directory, target = self._target(run_id, normalized_name)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.parent / f".{target.name}.{uuid4().hex}.tmp"
        temporary.write_bytes(content)
        os.replace(temporary, target)
        digest = hashlib.sha256(content).hexdigest()
        return StoredArtifact(
            uri=target.relative_to(self._root).as_posix(),
            content_hash=f"sha256:{digest}",
            size_bytes=len(content),
        )

    def _write_unique(self, run_id: UUID, name: str, content: bytes) -> StoredArtifact:
        normalized_name = self._safe_name(name)
        run_directory, target = self._target(run_id, normalized_name)
        target.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(content)
        except Exception:
            target.unlink(missing_ok=True)
            raise
        digest = hashlib.sha256(content).hexdigest()
        return StoredArtifact(
            uri=target.relative_to(self._root).as_posix(),
            content_hash=f"sha256:{digest}",
            size_bytes=len(content),
        )

    def _target(self, run_id: UUID, normalized_name: str) -> tuple[Path, Path]:
        run_directory = (self._root / str(run_id)).resolve(strict=False)
        if not run_directory.is_relative_to(self._root):
            raise ValueError("artifact path escapes configured root")
        run_directory.mkdir(parents=True, exist_ok=True)
        target = (run_directory / normalized_name).resolve(strict=False)
        if not target.is_relative_to(self._root):
            raise ValueError("artifact path escapes configured root")
        return run_directory, target

    async def read(self, uri: str) -> bytes:
        return await asyncio.to_thread(self._read, uri)

    async def erase(self, uri: str) -> None:
        target = (self._root / uri).resolve(strict=False)
        if not target.is_relative_to(self._root) or target == self._root:
            raise ValueError("artifact path escapes configured root")
        await asyncio.to_thread(target.unlink, missing_ok=True)

    def _read(self, uri: str) -> bytes:
        target = (self._root / uri).resolve(strict=False)
        if not target.is_relative_to(self._root):
            raise ValueError("artifact path escapes configured root")
        return target.read_bytes()

    @staticmethod
    def _safe_name(name: str) -> str:
        """接受**运行目录内的相对路径**（`reports/summary.md`），拒绝任何逃逸写法。

        `file_write` 会把 `reports/summary.md` 这类子路径登记成产物；早期实现只允许纯文件名，
        于是带子目录的写入会先落盘、再在登记时抛错，留下"文件在里面但产物不存在"的状态。
        """

        normalized = name.strip().replace("\\", "/")
        if not normalized or normalized.startswith("/"):
            raise ValueError("artifact name must be a relative path")
        parts = [part for part in normalized.split("/") if part not in {"", "."}]
        if not parts or any(part == ".." for part in parts):
            raise ValueError("artifact name must not escape its run directory")
        return "/".join(parts)


class ArtifactService:
    """先保存内容，再把可校验的引用写入数据库。"""

    def __init__(
        self,
        store: ArtifactStore,
        session_factory: async_sessionmaker[AsyncSession],
        lease_guard: LeaseGuard | None = None,
    ) -> None:
        self._lease_guard = lease_guard
        self._store = store
        self._session_factory = session_factory

    async def read(self, uri: str) -> bytes:
        """通过受控 Store 读取已经登记的内容。"""

        return await self._store.read(uri)

    async def create(
        self,
        *,
        run_id: UUID,
        name: str,
        content: bytes,
        artifact_type: str,
        attributes: dict[str, object] | None = None,
    ) -> ArtifactRecord:
        if self._lease_guard is not None:
            if self._lease_guard.lease.run_id != run_id:
                raise ValueError("artifact run does not match lease")
            async with self._session_factory() as session:
                await self._lease_guard.check(session)
        stored = await self._store.write(run_id, name, content)
        async with UnitOfWork(self._session_factory) as unit:
            if self._lease_guard is not None:
                await self._lease_guard.check(unit.session)
                await check_run_references(unit.session, run_id)
            record = ArtifactRecord(
                run_id=run_id,
                type=artifact_type,
                uri=stored.uri,
                content_hash=stored.content_hash,
                size_bytes=stored.size_bytes,
                attributes=attributes or {},
            )
            unit.session.add(record)
            await unit.session.flush()
            await unit.commit()
            return record

    async def create_unique(
        self,
        *,
        run_id: UUID,
        name: str,
        content: bytes,
        artifact_type: str,
        attributes: dict[str, object] | None = None,
    ) -> ArtifactRecord:
        """创建不可覆盖的 Artifact，并登记内容哈希与元数据。"""

        if self._lease_guard is not None:
            if self._lease_guard.lease.run_id != run_id:
                raise ValueError("artifact run does not match lease")
            async with self._session_factory() as session:
                await self._lease_guard.check(session)
        stored = await self._store.write_unique(run_id, name, content)
        return await self._register(
            run_id=run_id,
            stored=stored,
            artifact_type=artifact_type,
            attributes=attributes,
        )

    async def create_or_replace(
        self,
        *,
        run_id: UUID,
        name: str,
        content: bytes,
        artifact_type: str,
        attributes: dict[str, object] | None = None,
    ) -> ArtifactRecord:
        """按路径写入并**就地更新**登记记录（同一 Run 内同名文件只有一条产物）。

        这是 `file_write` 用的语义：它按路径覆盖写文件，因此登记记录必须跟着更新，
        否则旧记录会指向新字节、下载时哈希校验必然失败（那是"记录与存储不一致"，
        会被 F-04 的导出检查拒绝，但用户看到的是一条永远下载不了的产物）。
        """

        if self._lease_guard is not None:
            if self._lease_guard.lease.run_id != run_id:
                raise ValueError("artifact run does not match lease")
            async with self._session_factory() as session:
                await self._lease_guard.check(session)
        stored = await self._store.write(run_id, name, content)
        async with UnitOfWork(self._session_factory) as unit:
            if self._lease_guard is not None:
                await self._lease_guard.check(unit.session)
                await check_run_references(unit.session, run_id)
            existing = await unit.session.scalar(
                select(ArtifactRecord).where(
                    ArtifactRecord.run_id == run_id,
                    ArtifactRecord.uri == stored.uri,
                )
            )
            if existing is None:
                record = ArtifactRecord(
                    run_id=run_id,
                    type=artifact_type,
                    uri=stored.uri,
                    content_hash=stored.content_hash,
                    size_bytes=stored.size_bytes,
                    attributes=attributes or {},
                )
                unit.session.add(record)
            else:
                record = existing
                record.type = artifact_type
                record.content_hash = stored.content_hash
                record.size_bytes = stored.size_bytes
                record.attributes = {**record.attributes, **(attributes or {})}
            await unit.session.flush()
            await unit.commit()
            return record

    async def _register(
        self,
        *,
        run_id: UUID,
        stored: StoredArtifact,
        artifact_type: str,
        attributes: dict[str, object] | None,
    ) -> ArtifactRecord:
        async with UnitOfWork(self._session_factory) as unit:
            if self._lease_guard is not None:
                await self._lease_guard.check(unit.session)
                await check_run_references(unit.session, run_id)
            record = ArtifactRecord(
                run_id=run_id,
                type=artifact_type,
                uri=stored.uri,
                content_hash=stored.content_hash,
                size_bytes=stored.size_bytes,
                attributes=attributes or {},
            )
            unit.session.add(record)
            await unit.session.flush()
            await unit.commit()
            return record
