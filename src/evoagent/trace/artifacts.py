"""Artifact 二进制存储与数据库元数据登记。"""

import asyncio
import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from evoagent.db.models import ArtifactRecord
from evoagent.db.unit_of_work import UnitOfWork


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
        run_directory = (self._root / str(run_id)).resolve(strict=False)
        if not run_directory.is_relative_to(self._root):
            raise ValueError("artifact path escapes configured root")
        run_directory.mkdir(parents=True, exist_ok=True)
        target = run_directory / normalized_name
        temporary = run_directory / f".{normalized_name}.{uuid4().hex}.tmp"
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
        run_directory = (self._root / str(run_id)).resolve(strict=False)
        if not run_directory.is_relative_to(self._root):
            raise ValueError("artifact path escapes configured root")
        run_directory.mkdir(parents=True, exist_ok=True)
        target = run_directory / normalized_name
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

    async def read(self, uri: str) -> bytes:
        return await asyncio.to_thread(self._read, uri)

    def _read(self, uri: str) -> bytes:
        target = (self._root / uri).resolve(strict=False)
        if not target.is_relative_to(self._root):
            raise ValueError("artifact path escapes configured root")
        return target.read_bytes()

    @staticmethod
    def _safe_name(name: str) -> str:
        normalized = name.strip()
        if not normalized or Path(normalized).name != normalized or normalized in {".", ".."}:
            raise ValueError("artifact name must be a plain file name")
        return normalized


class ArtifactService:
    """先保存内容，再把可校验的引用写入数据库。"""

    def __init__(
        self,
        store: ArtifactStore,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
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
        stored = await self._store.write(run_id, name, content)
        async with UnitOfWork(self._session_factory) as unit:
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

        stored = await self._store.write_unique(run_id, name, content)
        async with UnitOfWork(self._session_factory) as unit:
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
