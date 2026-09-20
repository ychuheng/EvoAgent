"""截断前保存脱敏后的完整工具输出；读取必须由 Run ID 授权。"""

import json
from uuid import uuid4

from evoagent.db.models import ArtifactRecord
from evoagent.memory.policy import redact
from evoagent.memory.repository import check_run_references
from evoagent.memory.schema import MemoryError
from evoagent.sessions.service import text_hash
from evoagent.tasks.lease import LeaseLostError
from evoagent.tools.base import ToolExecutionError, ToolPermissionError


class ToolOutputStore:
    def __init__(self, run_id, service, session_factory):
        self.run_id = run_id
        self.service = service
        self.factory = session_factory

    async def preserve(self, content, limit):
        async with self.factory() as session:
            try:
                await check_run_references(session, self.run_id)
            except MemoryError:
                return "[context_source_revoked: output body removed]"[:limit]
        safe = redact(content)
        if len(safe) <= limit:
            return safe
        try:
            record = await self.service.create_unique(
                run_id=self.run_id,
                name=f"tool-output-{uuid4().hex}.txt",
                content=safe.encode("utf-8"),
                artifact_type="tool_output",
                attributes={"redacted": True},
            )
        except LeaseLostError:
            raise
        except (OSError, ValueError):
            marker = "[artifact_store_failed: full output unavailable]"
            return (marker + "\n" + safe)[:limit]
        reference = json.dumps(
            {
                "artifact_id": str(record.id),
                "hash": record.content_hash,
                "total_chars": len(safe),
                "read_tool": "artifact_read",
            }
        )
        if len(reference) > limit:
            return "[output archived; preview budget too small for reference]"[:limit]
        return reference + "\n" + safe[: max(0, limit - len(reference) - 1)]

    async def read(self, artifact_id, offset, limit):
        async with self.factory() as session:
            try:
                await check_run_references(session, self.run_id)
            except MemoryError as error:
                raise ToolPermissionError("context source revoked") from error
            record = await session.get(ArtifactRecord, artifact_id)
            if record is None or record.run_id != self.run_id or record.attributes.get("erased"):
                raise ToolPermissionError("artifact is outside current run or erased")
        data = await self.service.read(record.uri)
        content = data.decode("utf-8")
        if text_hash(content) != record.content_hash:
            raise ToolExecutionError("artifact hash mismatch")
        return json.dumps(
            {
                "artifact_id": str(record.id),
                "offset": offset,
                "next_offset": min(len(content), offset + limit),
                "total_chars": len(content),
                "content": content[offset : offset + limit],
            },
            ensure_ascii=False,
        )
