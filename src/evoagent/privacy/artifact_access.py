"""artifact 与派生正文进入模型上下文之前的注入门禁（S0b / M-A0）。

为什么需要（改造方案 §2.1 第 7～9 条）：`ToolOutputStore.read()` 只校验归属、撤销与
`content_hash`，**不随敏感规则复查正文**。规则扩容后，用旧规则写过、新规则能识别的
秘密仍可经 `artifact_read` 或恢复路径被拉回上下文。本模块把"注入前必须过当前策略"
做成一道可测试的门。

三阶段部署（§16 `M-A0`）中的**第一步**：本模块不新增数据库列、不改 ORM 定义，因此
可以在**旧 schema 上直接部署**——检查元数据通过 `getattr` 读取，字段不存在时一律按
`unchecked` 处理（每次注入都复查，fail-safe）。等第二步加上列与迁移后，同一份代码会
自动开始复用检查结果，无需再改这里。

失败语义：一律拒绝注入，**不返回旧正文兜底**。
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select

from evoagent.db.unit_of_work import UnitOfWork
from evoagent.memory.repository import check_run_references
from evoagent.memory.schema import MemoryError
from evoagent.privacy.redaction import POLICY_VERSION, RedactionResult, redact_text_result
from evoagent.tools.base import ToolExecutionError, ToolPermissionError

#: 允许注入模型的 artifact 类型白名单。其余类型（评测报告、skill 来源、下载产物等）
#: 没有"重新注入模型"的用途，一律不因门禁通过而获得注入权限。
INJECTABLE_ARTIFACT_TYPES = frozenset({"tool_output", "context_source"})

#: 扫描预算来自实测：现有原语中位 48.0 ms/MB、最差 59.8 ms/MB，见
#: docs/reports/artifact-scan-budget-2026-10-08.json。10 MB ≈ 480 ms，取 500 ms 上限。
#: 超过上限**拒绝注入**而不是降级放行（§2.1 第 8 条）。
SCAN_BUDGET_MS = 500
MAX_SCAN_BYTES = 10 * 1024 * 1024

BLOCK_EVENT_TYPE = "artifact.injection_blocked"

STATUS_UNCHECKED = "unchecked"
STATUS_VERIFIED = "verified"
STATUS_QUARANTINED = "quarantined"
STATUS_NOT_APPLICABLE = "not_applicable"


class ArtifactSensitiveContent(ToolPermissionError):
    """正文命中当前敏感规则，已隔离并拒绝注入。"""

    code = "artifact_sensitive_content"


class ArtifactCheckUnavailable(ToolPermissionError):
    """无法完成当前策略的检查（超预算、策略版本未知、检查不可用）——fail closed。"""

    code = "artifact_check_unavailable"


class ArtifactNotInjectable(ToolPermissionError):
    """该 artifact 类型不在注入白名单内。"""

    code = "artifact_not_injectable"


@dataclass(frozen=True, slots=True)
class ArtifactCheckState:
    """从记录上读到的检查元数据；字段缺失时表示"旧 schema，未检查"。"""

    status: str = STATUS_UNCHECKED
    policy_version: int | None = None
    checked_hash: str | None = None

    @property
    def reusable(self) -> bool:
        return self.status == STATUS_VERIFIED and self.policy_version == POLICY_VERSION


def _check_state(record: Any) -> ArtifactCheckState:
    """用 `getattr` 读取检查元数据，使同一份代码在加列前后都能运行。"""

    status = getattr(record, "redaction_status", None) or STATUS_UNCHECKED
    return ArtifactCheckState(
        status=str(status),
        policy_version=getattr(record, "redaction_policy_version", None),
        checked_hash=getattr(record, "redaction_checked_hash", None),
    )


def _supports_check_columns() -> bool:
    """检查元数据列是否已在 ORM 中声明（第二步的迁移之后才为真）。"""

    from evoagent.db.models import ArtifactRecord

    return "redaction_status" in ArtifactRecord.__table__.columns


class ArtifactInjectionGuard:
    """注入前的统一门禁：授权 → hash → 当前策略复查 → 复用或隔离。"""

    def __init__(
        self,
        *,
        session_factory,
        artifact_store,
        scan_budget_ms: int = SCAN_BUDGET_MS,
        max_scan_bytes: int = MAX_SCAN_BYTES,
    ) -> None:
        self._session_factory = session_factory
        self._store = artifact_store
        self._scan_budget_ms = scan_budget_ms
        self._max_scan_bytes = max_scan_bytes

    async def read_verified_text(
        self,
        *,
        artifact_id: UUID,
        run_id: UUID,
        purpose: str,
    ) -> str:
        """读取整份正文，确认它通过当前策略后才允许调用方分页。

        先复查**整份**正文再分页，是为了防止调用方用 offset/limit 把秘密切碎绕过检测。
        """

        from evoagent.db.models import ArtifactRecord

        async with self._session_factory() as session:
            try:
                await check_run_references(session, run_id)
            except MemoryError as error:
                raise ToolPermissionError("context source revoked") from error
            record = await session.get(ArtifactRecord, artifact_id)
            if record is None or record.run_id != run_id or record.attributes.get("erased"):
                raise ToolPermissionError("artifact is outside current run or erased")
            artifact_type = str(record.type)
            uri = record.uri
            expected_hash = record.content_hash
            state = _check_state(record)

        if artifact_type not in INJECTABLE_ARTIFACT_TYPES:
            raise ArtifactNotInjectable(f"artifact type is not injectable: {artifact_type}")

        data = await self._store.read(uri)
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as error:
            # 二进制不进入模型上下文；它不是"需要脱敏"，而是"不该被读"。
            raise ArtifactNotInjectable("artifact is not utf-8 text") from error
        actual_hash = _text_hash(text)
        if actual_hash != expected_hash:
            raise ToolExecutionError("artifact hash mismatch")

        if state.status == STATUS_QUARANTINED:
            await self._ensure_block_event(
                run_id=run_id,
                source_id=artifact_id,
                source_hash=actual_hash,
                categories=(),
                purpose=purpose,
            )
            raise ArtifactSensitiveContent("artifact was quarantined by an earlier check")
        if state.status == STATUS_NOT_APPLICABLE:
            # `not_applicable` 表示"脱敏规则不适用"，它不授予注入权限。
            raise ArtifactNotInjectable("artifact is marked not_applicable for injection")
        if state.reusable and state.checked_hash == actual_hash:
            return text

        checked = await self._scan(
            run_id=run_id,
            source_id=artifact_id,
            source_hash=actual_hash,
            text=text,
            purpose=purpose,
        )
        if checked.redacted:
            await self._quarantine(artifact_id=artifact_id, run_id=run_id, source_hash=actual_hash)
            await self._ensure_block_event(
                run_id=run_id,
                source_id=artifact_id,
                source_hash=actual_hash,
                categories=checked.categories,
                purpose=purpose,
            )
            raise ArtifactSensitiveContent(
                "artifact contains content matched by the current sensitive policy"
            )
        await self.mark_verified(artifact_id=artifact_id, run_id=run_id, source_hash=actual_hash)
        return text

    async def verify_derived_text(
        self,
        *,
        text: str,
        run_id: UUID,
        source_id: str,
        source_hash: str,
        purpose: str,
    ) -> str:
        """校验数据库里的派生正文（archive.summary、恢复摘要、派生工具消息等）。

        这些载体没有 artifact 行可复用检查结果，因此**每次注入都按当前规则检查**，
        不引入未设计的缓存列（§2.1 第 9 条）。
        """

        if _text_hash(text) != source_hash:
            raise ToolExecutionError("derived text hash mismatch")
        checked = await self._scan(
            run_id=run_id,
            source_id=source_id,
            source_hash=source_hash,
            text=text,
            purpose=purpose,
        )
        if checked.redacted:
            await self._ensure_block_event(
                run_id=run_id,
                source_id=source_id,
                source_hash=source_hash,
                categories=checked.categories,
                purpose=purpose,
            )
            raise ArtifactSensitiveContent("derived text matched the current sensitive policy")
        return text

    async def _scan(
        self,
        *,
        run_id: UUID,
        source_id: Any,
        source_hash: str,
        text: str,
        purpose: str,
    ) -> RedactionResult:
        """在扫描预算内复查整份正文；超预算或检查不可用一律拒绝注入。"""

        encoded = len(text.encode("utf-8"))
        if encoded > self._max_scan_bytes:
            await self._ensure_block_event(
                run_id=run_id,
                source_id=source_id,
                source_hash=source_hash,
                categories=(),
                purpose=purpose,
                reason="scan_budget_exceeded",
            )
            raise ArtifactCheckUnavailable(
                f"artifact is larger than the scan budget: {encoded} > {self._max_scan_bytes} bytes"
            )
        started = time.perf_counter()
        result = redact_text_result(text)
        elapsed_ms = (time.perf_counter() - started) * 1000
        if elapsed_ms > self._scan_budget_ms:
            await self._ensure_block_event(
                run_id=run_id,
                source_id=source_id,
                source_hash=source_hash,
                categories=result.categories,
                purpose=purpose,
                reason="scan_timeout",
            )
            raise ArtifactCheckUnavailable(
                f"sensitive scan exceeded the budget: {elapsed_ms:.0f}ms > {self._scan_budget_ms}ms"
            )
        return result

    async def mark_verified(self, *, artifact_id: UUID, run_id: UUID, source_hash: str) -> None:
        """登记可复用的检查结果；未加列时静默跳过（第一步部署的正常路径）。

        两处调用：读路径复查通过之后，以及**写入路径**——`ToolOutputStore.preserve()`
        落盘的正文本身就是脱敏后的安全投影，因此写时即可登记，不必再扫一遍。
        """

        if not _supports_check_columns():
            return
        from evoagent.db.models import ArtifactRecord

        async with UnitOfWork(self._session_factory) as unit:
            record = await unit.session.get(ArtifactRecord, artifact_id, with_for_update=True)
            if record is None or record.run_id != run_id or record.content_hash != source_hash:
                return
            record.redaction_policy_version = POLICY_VERSION
            record.redaction_checked_hash = source_hash
            record.redaction_status = STATUS_VERIFIED
            await unit.commit()

    async def _quarantine(self, *, artifact_id: UUID, run_id: UUID, source_hash: str) -> None:
        """隔离命中项：不改 bytes/hash，也不原地重写旧 artifact。"""

        if not _supports_check_columns():
            return
        from evoagent.db.models import ArtifactRecord

        async with UnitOfWork(self._session_factory) as unit:
            record = await unit.session.get(ArtifactRecord, artifact_id, with_for_update=True)
            if record is None or record.run_id != run_id:
                return
            record.redaction_policy_version = POLICY_VERSION
            record.redaction_checked_hash = source_hash
            record.redaction_status = STATUS_QUARANTINED
            await unit.commit()

    async def _ensure_block_event(
        self,
        *,
        run_id: UUID,
        source_id: Any,
        source_hash: str,
        categories: tuple[str, ...],
        purpose: str,
        reason: str = "sensitive_content",
    ) -> None:
        """在**独立短事务**里记录阻断证据，幂等键为
        (source_id, content_hash, policy_version, event_type)。

        事件不含正文；它必须在抛错之前提交，否则读取失败的回滚会吞掉隔离记录。
        """

        key = {
            "artifact_id": str(source_id),
            "content_hash": source_hash,
            "policy_version": POLICY_VERSION,
        }
        async with UnitOfWork(self._session_factory) as unit:
            existing = await unit.session.scalars(
                select(_event_model())
                .where(
                    _event_model().run_id == run_id,
                    _event_model().event_type == BLOCK_EVENT_TYPE,
                )
                .order_by(_event_model().sequence.desc())
                .limit(200)
            )
            for event in existing:
                payload = event.payload or {}
                if all(payload.get(name) == value for name, value in key.items()):
                    return
            await unit.events.append(
                run_id=run_id,
                event_type=BLOCK_EVENT_TYPE,
                payload={
                    **key,
                    "rule_categories": list(categories),
                    "purpose": purpose,
                    "reason": reason,
                },
                created_at=datetime.now(UTC),
            )
            await unit.commit()


def _event_model():
    from evoagent.db.models import RunEventRecord

    return RunEventRecord


def _text_hash(text: str) -> str:
    from evoagent.sessions.service import text_hash

    return text_hash(text)
