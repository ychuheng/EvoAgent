"""截断前保存脱敏后的完整工具输出；读取统一经过注入门禁。"""

import json
from uuid import uuid4

from evoagent.memory.repository import check_run_references
from evoagent.memory.schema import MemoryError
from evoagent.privacy.artifact_access import ArtifactInjectionGuard
from evoagent.privacy.redaction import redact_text
from evoagent.tasks.lease import LeaseLostError


class ToolOutputStore:
    """长工具输出的归档与回读。

    两条路径的分工（改造方案 §2.1 第 8～9 条）：

    - **写入**：先把正文过一遍当前敏感原语再落盘，落盘后登记"已按当前策略检查"
      （未加检查元数据列时静默跳过）；因此归档正文本身就是安全投影。
    - **读取**：一律经过 `ArtifactInjectionGuard` —— 授权、撤销/擦除、整份正文的
      hash 与当前策略复查、必要时隔离并拒绝注入。**不再**只校验 hash 就返回内容。
    """

    def __init__(self, run_id, service, session_factory):
        self.run_id = run_id
        self.service = service
        self.factory = session_factory
        self._guard = ArtifactInjectionGuard(
            session_factory=session_factory, artifact_store=service
        )

    async def preserve(self, content, limit):
        async with self.factory() as session:
            try:
                await check_run_references(session, self.run_id)
            except MemoryError:
                return "[context_source_revoked: output body removed]"[:limit]
        safe = redact_text(content)
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
        await self._guard.mark_verified(
            artifact_id=record.id, run_id=self.run_id, source_hash=record.content_hash
        )
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
        """回读归档正文；授权、hash 与当前策略复查全部由门禁负责。

        门禁先复查**整份**正文再返回，这里才做分页——分页不能绕过检测。
        """

        content = await self._guard.read_verified_text(
            artifact_id=artifact_id, run_id=self.run_id, purpose="artifact_read"
        )
        return json.dumps(
            {
                "artifact_id": str(artifact_id),
                "offset": offset,
                "next_offset": min(len(content), offset + limit),
                "total_chars": len(content),
                "content": content[offset : offset + limit],
            },
            ensure_ascii=False,
        )
