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

import asyncio
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from time import monotonic
from typing import Any
from uuid import UUID

from sqlalchemy import select

from evoagent.db.unit_of_work import UnitOfWork
from evoagent.memory.repository import check_run_references
from evoagent.memory.schema import MemoryError
from evoagent.privacy.redaction import (
    POLICY_VERSION,
    RedactionResult,
    redact_text,
)
from evoagent.privacy.scanner import ScanLimits, ScanUnavailable, shared_scanner
from evoagent.tools.base import ToolExecutionError, ToolPermissionError

#: 允许注入模型的 artifact 类型白名单。其余类型（评测报告、skill 来源、下载产物等）
#: 没有"重新注入模型"的用途，一律不因门禁通过而获得注入权限。
INJECTABLE_ARTIFACT_TYPES = frozenset({"tool_output", "context_source"})
# Checking feedback evidence does not grant these downloads model injection.
EVIDENCE_TEXT_ARTIFACT_TYPES = INJECTABLE_ARTIFACT_TYPES | frozenset(
    {
        "text/plain",
        "text/markdown",
        "text/csv",
        "application/json",
        "text/x-python",
        "application/yaml",
        "text/html",
        "application/xml",
    }
)

#: 全量扫描采用有界读取、CPU/墙钟截止时间及可终止子进程。
SCAN_BUDGET_MS = 1000
MAX_SCAN_BYTES = 8 * 1024 * 1024

BLOCK_EVENT_TYPE = "artifact.injection_blocked"

#: 单件人工复核（§2.4）的三个事件类型。
REVIEW_REQUESTED_EVENT = "artifact.quarantine_review_requested"
REVIEW_CLEARED_EVENT = "artifact.quarantine_cleared"
REVIEW_REJECTED_EVENT = "artifact.quarantine_review_rejected"
REVIEW_EVENT_TYPES = (REVIEW_REQUESTED_EVENT, REVIEW_CLEARED_EVENT, REVIEW_REJECTED_EVENT)

OUTCOME_CLEARED = "cleared"
OUTCOME_REJECTED = "rejected"

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


class QuarantineReviewStale(ToolPermissionError):
    """复核条件已过期：内容 hash、策略版本或隔离状态在复核期间变了（路由映射 409）。"""

    code = "quarantine_review_stale"


@dataclass(frozen=True, slots=True)
class QuarantineReviewOutcome:
    """一次人工复核的结果；`rejected` 表示**继续隔离**，不是接口失败。"""

    artifact_id: UUID
    outcome: str
    policy_version: int | None = None
    checked_hash: str | None = None
    reason: str | None = None
    categories: tuple[str, ...] = ()
    replayed: bool = False


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
        artifact_store=None,
        scan_budget_ms: int = SCAN_BUDGET_MS,
        max_scan_bytes: int = MAX_SCAN_BYTES,
        settings=None,
    ) -> None:
        # 只做派生正文复查（`verify_derived_text`）的调用方不需要 artifact 存储；
        # 那种情况下 `read_verified_text` 会显式报错，而不是静默放行。
        self._session_factory = session_factory
        self._store = artifact_store
        self._scan_cache_enabled = bool(getattr(settings, "runtime_scan_cache_enabled", False))
        self._scan_budget_ms = settings.artifact_scan_wall_ms if settings else scan_budget_ms
        self._max_scan_bytes = (
            settings.artifact_scan_inline_max_bytes if settings else max_scan_bytes
        )
        self._limits = ScanLimits(
            max_bytes=self._max_scan_bytes,
            wall_ms=self._scan_budget_ms,
            cpu_ms=settings.artifact_scan_cpu_ms if settings else 250,
            concurrency=settings.artifact_scan_concurrency if settings else 2,
            queue_size=settings.artifact_scan_queue_size if settings else 16,
        )
        self._offline_limits = ScanLimits(
            max_bytes=64 * 1024 * 1024,
            cpu_ms=2000,
            wall_ms=5000,
            concurrency=self._limits.concurrency,
            queue_size=self._limits.queue_size,
        )

    async def _read_bounded(self, uri, *, limits, deadline):
        reader = getattr(self._store, "read_bounded", None)
        if reader is None:
            raise ArtifactCheckUnavailable("artifact store has no bounded reader")
        try:
            async with asyncio.timeout_at(deadline):
                return await reader(uri, max_bytes=limits.max_bytes)
        except (ValueError, TimeoutError) as error:
            raise ArtifactCheckUnavailable(
                "artifact bounded read limit or deadline exceeded"
            ) from error

    async def read_verified_text(self, *, artifact_id: UUID, run_id: UUID, purpose: str) -> str:
        """Only injection-whitelisted artifacts may return text to a model reader."""
        return await self._check_text_artifact(
            artifact_id=artifact_id,
            run_id=run_id,
            purpose=purpose,
            allowed_types=INJECTABLE_ARTIFACT_TYPES,
        )

    async def verify_feedback_evidence(
        self, *, artifact_id: UUID, run_id: UUID, expected_hash: str
    ) -> None:
        """Check selected text evidence without returning any body or widening injection."""
        await self._check_text_artifact(
            artifact_id=artifact_id,
            run_id=run_id,
            purpose="human_feedback_evidence",
            allowed_types=EVIDENCE_TEXT_ARTIFACT_TYPES,
            requested_hash=expected_hash,
        )

    async def _check_text_artifact(
        self, *, artifact_id: UUID, run_id: UUID, purpose: str, allowed_types, requested_hash=None
    ) -> str:
        """Shared bounded whole-text check; permissions remain caller-specific."""
        if self._store is None:
            raise RuntimeError("artifact store is required for read_verified_text")
        started = monotonic()

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
            size_bytes = record.size_bytes
            state = _check_state(record)
            offline_verified = (
                state.reusable and record.attributes.get("redaction_scan_mode") == "offline"
            )

        if requested_hash is not None and expected_hash != requested_hash:
            raise ToolExecutionError("artifact evidence hash changed")
        if artifact_type not in allowed_types:
            raise ArtifactNotInjectable(f"artifact type is not injectable: {artifact_type}")
        if state.policy_version is not None and state.policy_version > POLICY_VERSION:
            raise ArtifactCheckUnavailable("runtime sensitive policy is older than artifact policy")

        # 先按**登记尺寸**（来自数据库，无需读盘）做预算判断：超过预算的正文根本
        # 不进内存，而不是读进来再拒绝（F3）。
        limits = self._offline_limits if offline_verified else self._limits
        deadline = started + limits.wall_ms / 1000
        if size_bytes > limits.max_bytes:
            await self._ensure_block_event(
                run_id=run_id,
                source_id=artifact_id,
                source_hash=expected_hash,
                categories=(),
                purpose=purpose,
                reason="scan_budget_exceeded",
            )
            raise ArtifactCheckUnavailable(
                f"artifact is larger than the scan budget: "
                f"{size_bytes} > {self._max_scan_bytes} bytes"
            )

        data = await self._read_bounded(uri, limits=limits, deadline=deadline)
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
            # 复用分支同样要复验：读盘期间可能已被擦除/撤销/隔离（F2）。
            await self._reverify(
                artifact_id=artifact_id,
                run_id=run_id,
                expected_hash=actual_hash,
                purpose=purpose,
                allowed_types=allowed_types,
            )
            return text

        checked = await self._scan(
            run_id=run_id,
            source_id=artifact_id,
            source_hash=actual_hash,
            text=text,
            purpose=purpose,
            deadline=deadline,
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
        # 返回前再复验一次：扫描期间可能发生了擦除/撤销/隔离（F2）。这是 §2.1 第 8 条
        # 要求的"提交和返回前复验授权及 content_hash"。
        await self._reverify(
            artifact_id=artifact_id,
            run_id=run_id,
            expected_hash=actual_hash,
            purpose=purpose,
            allowed_types=allowed_types,
        )
        return text

    async def _reverify(
        self, *, artifact_id: UUID, run_id: UUID, expected_hash: str, purpose: str, allowed_types
    ) -> None:
        """返回注入内容之前的最后一道复验。

        读取与扫描是分开的短事务，中间必然存在窗口；窗口内被擦除、撤销或隔离时
        **必须拒绝**，不能把已经读进内存的正文交出去。
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
            if (
                record.type not in allowed_types
                or _check_state(record).status == STATUS_NOT_APPLICABLE
            ):
                raise ArtifactNotInjectable("artifact permissions changed during read")
            if record.content_hash != expected_hash:
                raise ToolExecutionError("artifact hash mismatch")
            if _check_state(record).status == STATUS_QUARANTINED:
                # 与首次阻断共用同一个幂等键，不会重复追加事件。
                await self._ensure_block_event(
                    run_id=run_id,
                    source_id=artifact_id,
                    source_hash=expected_hash,
                    categories=(),
                    purpose=purpose,
                    reason="quarantined_during_read",
                )
                raise ArtifactSensitiveContent("artifact was quarantined during the read")

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

    async def _rescan(
        self, text: str, *, deadline=None, limits=None
    ) -> tuple[RedactionResult | None, str | None]:
        """有界排队与可终止子进程扫描；退出前回收进程，不遗留后台 CPU 工作。"""

        limits = limits or self._limits
        try:
            scanner = shared_scanner(limits)
            scan = scanner.scan_identical if self._scan_cache_enabled else scanner.scan
            result = await scan(text, limits, deadline=deadline)
        except ScanUnavailable as error:
            return None, error.reason
        return result, None

    async def _scan(
        self,
        *,
        run_id: UUID,
        source_id: Any,
        source_hash: str,
        text: str,
        purpose: str,
        deadline=None,
    ) -> RedactionResult:
        """在扫描预算内复查整份正文；超预算或检查不可用一律拒绝注入。"""

        result, reason = await self._rescan(text, deadline=deadline)
        if reason is not None:
            await self._ensure_block_event(
                run_id=run_id,
                source_id=source_id,
                source_hash=source_hash,
                categories=result.categories if result is not None else (),
                purpose=purpose,
                reason=reason,
            )
            if reason == "scan_budget_exceeded":
                encoded = len(text.encode("utf-8"))
                raise ArtifactCheckUnavailable(
                    f"artifact is larger than the scan budget: "
                    f"{encoded} > {self._max_scan_bytes} bytes"
                )
            raise ArtifactCheckUnavailable(
                f"sensitive scan exceeded the budget of {self._scan_budget_ms}ms"
            )
        assert result is not None
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
            if getattr(record, "redaction_status", None) == STATUS_QUARANTINED:
                # 并发隔离优先：读路径的"通过"不得把刚被隔离的状态覆盖回 verified（F2）。
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

    async def clear_quarantine(
        self,
        *,
        artifact_id: UUID,
        actor: str,
        reason: str,
        expected_policy_version: int | None,
        expected_content_hash: str,
        client_request_id: str,
        offline: bool = False,
    ) -> QuarantineReviewOutcome:
        """单件人工复核：**通过才解除隔离**（改造方案 §2.4）。

        首版没有"忽略此秘密"的通用白名单：复核只按**当前**规则做全量复查，规则仍命中
        就继续隔离。所以它解决的是"误报已被纠正、规则包已升版"之后的解封，而不是让
        用户点一下就绕过检测。

        顺序按 §2.4：短事务里核对条件并落 `quarantine_review_requested` → 释放长事务
        后在扫描预算内全量复查 → 再加锁复验同一条件并提交结果。幂等键是
        `client_request_id`：重试不重复解封、不重复追加事件。
        """

        if self._store is None:
            raise RuntimeError("artifact store is required for clear_quarantine")
        if not actor.strip():
            raise ValueError("actor is required")
        if not client_request_id.strip():
            raise ValueError("client_request_id is required")
        # reason 必填，且**脱敏后**保存：理由里也可能被粘进真实凭据。
        safe_reason = redact_text(reason).strip()
        if not safe_reason:
            raise ValueError("reason is required")

        request_hash = _text_hash(
            json.dumps(
                {
                    "artifact_id": str(artifact_id),
                    "actor": actor,
                    "reason": reason,
                    "expected_policy_version": expected_policy_version,
                    "expected_content_hash": expected_content_hash,
                    "offline": offline,
                },
                sort_keys=True,
                ensure_ascii=False,
            )
        )
        limits = self._offline_limits if offline else self._limits
        deadline = monotonic() + limits.wall_ms / 1000

        async with UnitOfWork(self._session_factory) as unit:
            record = await self._lock_review_record(unit, artifact_id)
            await self._require_access(unit.session, record)
            # 幂等回放放在**锁内**并与 artifact 绑定：并发重复提交被这把行锁串行化，
            # 不会两个请求都判定"未见历史记录"而各写一次（F4）。
            replayed = await self._find_review(
                unit.session, artifact_id, client_request_id, request_hash
            )
            if replayed is not None:
                return replayed
            await self._require_reviewable(
                unit.session,
                record,
                expected_content_hash,
                expected_policy_version,
                offline=offline,
            )
            assert record is not None
            run_id, uri = record.run_id, record.uri
            await unit.events.append_once(
                dedupe_key=_text_hash(f"review:requested:{artifact_id}:{client_request_id}"),
                run_id=run_id,
                event_type=REVIEW_REQUESTED_EVENT,
                payload={
                    "artifact_id": str(artifact_id),
                    "actor": actor,
                    "request_body_hash": request_hash,
                    "offline": offline,
                    "reason": safe_reason,
                    "expected_content_hash": expected_content_hash,
                    "expected_policy_version": expected_policy_version,
                    "client_request_id": client_request_id,
                },
                created_at=datetime.now(UTC),
            )
            await unit.commit()

        # 复查在事务之外完成：长扫描不持有行锁（§2.4 明确要求先释放长事务）。
        actual_hash = None
        result = None
        reject_reason = None
        try:
            data = await self._read_bounded(uri, limits=limits, deadline=deadline)
            text = data.decode("utf-8")
            actual_hash = _text_hash(text)
            if actual_hash != expected_content_hash:
                raise QuarantineReviewStale("artifact content changed during review")
            result, reject_reason = await self._rescan(text, deadline=deadline, limits=limits)
        except ArtifactCheckUnavailable:
            reject_reason = "scan_budget_exceeded"
        except UnicodeDecodeError:
            reject_reason = "not_utf8"
        if reject_reason is not None:
            outcome = OUTCOME_REJECTED
            categories: tuple[str, ...] = ()
        elif result is not None and result.redacted:
            outcome = OUTCOME_REJECTED
            reject_reason = "sensitive_content"
            categories = result.categories
        else:
            outcome = OUTCOME_CLEARED
            categories = ()

        async with UnitOfWork(self._session_factory) as unit:
            record = await self._lock_review_record(unit, artifact_id)
            await self._require_access(unit.session, record)
            replayed = await self._find_review(
                unit.session, artifact_id, client_request_id, request_hash
            )
            if replayed is not None:
                return replayed
            # 加锁复验同一条件：复查期间被撤销/擦除/改动都不允许解封。
            await self._require_reviewable(
                unit.session,
                record,
                expected_content_hash,
                expected_policy_version,
                offline=offline,
            )
            assert record is not None
            if outcome == OUTCOME_CLEARED:
                # 只更新"绑定原内容的检查结论"；bytes/content_hash 永不因解封重算。
                record.redaction_status = STATUS_VERIFIED
                record.redaction_policy_version = POLICY_VERSION
                record.redaction_checked_hash = actual_hash
                record.attributes = {
                    **record.attributes,
                    "redaction_scan_mode": "offline" if offline else "inline",
                }
                event_type = REVIEW_CLEARED_EVENT
            else:
                if reject_reason == "sensitive_content":
                    record.redaction_status = STATUS_QUARANTINED
                    record.redaction_policy_version = POLICY_VERSION
                    record.redaction_checked_hash = actual_hash
                event_type = REVIEW_REJECTED_EVENT
            await unit.events.append_once(
                dedupe_key=_text_hash(f"review:terminal:{artifact_id}:{client_request_id}"),
                run_id=run_id,
                event_type=event_type,
                payload={
                    "artifact_id": str(artifact_id),
                    "actor": actor,
                    "client_request_id": client_request_id,
                    "request_body_hash": request_hash,
                    "outcome": outcome,
                    "reason": reject_reason,
                    "rule_categories": list(categories),
                    "policy_version": POLICY_VERSION,
                    # 成功事件同时保存实际复查的 hash，回放时才能给出完整结果（F4）；
                    # 它绑定的是**原内容**，不是新内容。
                    "checked_hash": actual_hash if outcome == OUTCOME_CLEARED else None,
                },
                created_at=datetime.now(UTC),
            )
            await unit.commit()

        return QuarantineReviewOutcome(
            artifact_id=artifact_id,
            outcome=outcome,
            policy_version=POLICY_VERSION if outcome == OUTCOME_CLEARED else None,
            checked_hash=actual_hash if outcome == OUTCOME_CLEARED else None,
            reason=reject_reason,
            categories=categories,
        )

    async def _lock_review_record(self, unit, artifact_id):
        record = await unit.session.get(_artifact_model(), artifact_id)
        if record is None:
            raise ToolPermissionError("artifact is unknown or erased")
        await unit.events.lock_run(record.run_id)
        return await unit.session.scalar(
            select(_artifact_model())
            .where(_artifact_model().id == artifact_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )

    async def _require_access(self, session, record):
        if record is None or record.attributes.get("erased"):
            raise ToolPermissionError("artifact is unknown or erased")
        if record.type not in EVIDENCE_TEXT_ARTIFACT_TYPES:
            raise ArtifactNotInjectable("artifact type has no supported text review")
        try:
            await check_run_references(session, record.run_id)
        except MemoryError as error:
            raise ToolPermissionError("context source revoked") from error

    async def _require_reviewable(
        self, session, record, expected_content_hash, expected_policy_version, *, offline=False
    ):
        await self._require_access(session, record)
        if record.content_hash != expected_content_hash:
            raise QuarantineReviewStale("artifact content hash changed")
        state = _check_state(record)
        if state.policy_version != expected_policy_version:
            raise QuarantineReviewStale("artifact policy version changed")
        if state.policy_version is not None and state.policy_version > POLICY_VERSION:
            raise QuarantineReviewStale("runtime sensitive policy is older than artifact policy")
        if not offline and state.status != STATUS_QUARANTINED:
            raise QuarantineReviewStale("artifact is not quarantined")
        if offline and state.status not in (STATUS_QUARANTINED, STATUS_UNCHECKED, STATUS_VERIFIED):
            raise QuarantineReviewStale("artifact cannot be reviewed")

    async def _find_review(self, session, artifact_id, client_request_id, request_hash):
        record = await session.get(_artifact_model(), artifact_id)
        events = tuple(
            await session.scalars(
                select(_event_model())
                .where(
                    _event_model().run_id == record.run_id,
                    _event_model().event_type.in_(REVIEW_EVENT_TYPES),
                    _event_model().payload["artifact_id"].as_string() == str(artifact_id),
                    _event_model().payload["client_request_id"].as_string() == client_request_id,
                )
                .order_by(_event_model().sequence.desc())
            )
        )
        for event in events:
            payload = event.payload or {}
            if payload.get("request_body_hash") != request_hash:
                raise QuarantineReviewStale(
                    "client_request_id reused with a different or unverifiable request"
                )
            if event.event_type == REVIEW_CLEARED_EVENT:
                return QuarantineReviewOutcome(
                    artifact_id=artifact_id,
                    outcome=OUTCOME_CLEARED,
                    policy_version=payload.get("policy_version"),
                    checked_hash=payload.get("checked_hash"),
                    replayed=True,
                )
            if event.event_type == REVIEW_REJECTED_EVENT:
                return QuarantineReviewOutcome(
                    artifact_id=artifact_id,
                    outcome=OUTCOME_REJECTED,
                    reason=payload.get("reason"),
                    categories=tuple(payload.get("rule_categories") or ()),
                    replayed=True,
                )
        return None

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
            await unit.events.lock_run(run_id)
            event = _event_model()
            existing = await unit.session.scalar(
                select(event.id)
                .where(
                    event.run_id == run_id,
                    event.event_type == BLOCK_EVENT_TYPE,
                    event.payload["artifact_id"].as_string() == key["artifact_id"],
                    event.payload["content_hash"].as_string() == key["content_hash"],
                    event.payload["policy_version"].as_integer() == POLICY_VERSION,
                )
                .limit(1)
            )
            if existing is not None:
                return
            await unit.events.append_once(
                dedupe_key=_text_hash(
                    json.dumps([BLOCK_EVENT_TYPE, str(source_id), source_hash, POLICY_VERSION])
                ),
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


def _artifact_model():
    from evoagent.db.models import ArtifactRecord

    return ArtifactRecord
