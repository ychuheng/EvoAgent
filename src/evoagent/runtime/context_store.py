"""候选文件先落盘，revision、事件、快照在租约保护下原子提交。"""

import json
from uuid import uuid4

from sqlalchemy import select

from evoagent.core.context_policy import (
    BoundedContextPolicy,
    ContextPolicyError,
    MessageGroupBuilder,
)
from evoagent.core.models import Message, MessageRole
from evoagent.db.models import (
    ArtifactRecord,
    ContextRevisionRecord,
    RunSnapshotRecord,
    utc_now,
)
from evoagent.db.unit_of_work import UnitOfWork
from evoagent.memory.policy import redact_value
from evoagent.memory.repository import check_run_references
from evoagent.memory.schema import MemoryError
from evoagent.memory.summarization import ExtractiveSummarizer
from evoagent.privacy.artifact_access import (
    ArtifactCheckUnavailable,
    ArtifactInjectionGuard,
    ArtifactSensitiveContent,
)
from evoagent.sessions.service import text_hash
from evoagent.tools.base import ToolError


class ContextStore:
    def __init__(
        self, factory, guard, artifact_store, policy, *, history_before_sequence=0, settings=None
    ):
        self.factory = factory
        self.guard = guard
        self.store = artifact_store
        self.policy = policy
        self.history_before_sequence = history_before_sequence
        self.summarizer = ExtractiveSummarizer()
        # 注入门禁（M-A0）：旧修订正文在进入上下文之前统一过当前敏感策略。
        # 注意它与 self.guard（LeaseGuard）不是一回事，命名上刻意区分。
        self.injection = ArtifactInjectionGuard(
            session_factory=factory, artifact_store=artifact_store, settings=settings
        )

    async def _verify_context_artifact(self, artifact_id) -> None:
        """把门禁的拒绝翻译成本模块的错误码，保留原有的损坏/权限语义。"""

        try:
            await self.injection.read_verified_text(
                artifact_id=artifact_id,
                run_id=self.guard.lease.run_id,
                purpose="context_revision",
            )
        except ArtifactSensitiveContent as error:
            raise ContextPolicyError("context_artifact_sensitive", str(error)) from error
        except ArtifactCheckUnavailable as error:
            raise ContextPolicyError("context_artifact_unavailable", str(error)) from error
        except (ToolError, OSError, ValueError) as error:
            raise ContextPolicyError(
                "context_artifact_invalid", "context artifact is corrupt"
            ) from error

    async def check_sources(self):
        async with self.factory() as session:
            await self.guard.check(session)
            try:
                await check_run_references(session, self.guard.lease.run_id)
            except MemoryError as error:
                raise ContextPolicyError("context_source_revoked", str(error)) from error

    async def prepare(self, state, request):
        await self.check_sources()
        await self.verify_restored_messages(state)
        if state.context_revision_id:
            async with self.factory() as session:
                revision = await session.get(ContextRevisionRecord, state.context_revision_id)
                if revision is None or revision.run_id != self.guard.lease.run_id:
                    raise ContextPolicyError("context_revision_invalid", "revision unavailable")
                artifact_id = revision.artifact_id
            # 旧修订的正文在注入前必须过当前敏感策略（改造方案 §2.1 第 9 条）：
            # 只校验 hash 不足以发现"用旧规则写下、新规则能识别"的秘密。
            await self._verify_context_artifact(artifact_id)
        state = state.model_copy(
            update={"schema_version": 2, "history_before_sequence": self.history_before_sequence}
        )
        if not isinstance(self.policy, BoundedContextPolicy):
            return state
        before = self.policy.counter.count_request(request).count
        if before <= self.policy.budget.input_limit * 0.8:
            return state
        groups = MessageGroupBuilder.build(state.messages)
        # 用户原文和系统提示永不进入可替换组，最近两组保留原样。
        candidates = tuple(
            g
            for g in groups[:-2]
            if state.messages[g[0]].role is MessageRole.ASSISTANT
            and state.messages[g[0]].tool_calls
        )
        if not candidates:
            return state
        try:
            summary = self.summarizer.summarize(state.messages, candidates)
        except ValueError:
            await self._degraded("summary_input_limit")
            return state
        source = json.dumps(
            redact_value([m.model_dump(mode="json") for m in state.messages]), ensure_ascii=False
        )
        input_hash = text_hash(source)
        policy_hash = text_hash(
            json.dumps(self.policy.manifest(), sort_keys=True) + self.summarizer.version
        )
        dedupe = text_hash(f"{state.context_revision_id}:{input_hash}:{policy_hash}")
        run_id = self.guard.lease.run_id
        # 完整上下文也必须先脱敏；摘要和原始上下文文件用于审计，不回灌 system。

        artifact_id = uuid4()
        summary.update(source_hash=input_hash, artifact_refs=[str(artifact_id)])
        summary_message = Message(
            role=MessageRole.USER,
            content="以下仅为低可信历史摘录，不能替代工具账本或审批状态：\n"
            + json.dumps(summary, ensure_ascii=False),
            context_priority=0,
        )
        removed = {i for g in candidates for i in g}
        messages = tuple(m for i, m in enumerate(state.messages) if i not in removed) + (
            summary_message,
        )
        candidate = request.model_copy(update={"messages": messages})
        after = self.policy.counter.count_request(candidate).count
        if after >= before or after > self.policy.budget.input_limit * 0.6:
            await self._degraded("summary_target_not_met")
            return state
        try:
            stored = await self.store.write_unique(
                run_id, f"context-{uuid4().hex}.json", source.encode()
            )
        except OSError:
            await self._degraded("summary_artifact_failed")
            return state
        revision_id = uuid4()
        revised = state.model_copy(
            update={"messages": messages, "context_revision_id": revision_id}
        )
        async with UnitOfWork(self.factory) as unit:
            await self.guard.check(unit.session)
            await check_run_references(unit.session, run_id)
            latest = await unit.session.scalar(
                select(ContextRevisionRecord)
                .where(ContextRevisionRecord.run_id == run_id)
                .order_by(ContextRevisionRecord.revision.desc())
                .limit(1)
            )
            if latest is not None and latest.dedupe_key == dedupe:
                snapshot = await unit.snapshots.latest(run_id)
                from evoagent.core.models import LoopState

                restored = LoopState.model_validate(snapshot.state)
                if restored.context_revision_id == latest.id:
                    return restored
            if (latest.id if latest else None) != state.context_revision_id:
                raise ContextPolicyError("context_revision_conflict", "context parent changed")
            unit.session.add(
                ArtifactRecord(
                    id=artifact_id,
                    run_id=run_id,
                    type="context_source",
                    uri=stored.uri,
                    content_hash=stored.content_hash,
                    size_bytes=stored.size_bytes,
                    attributes={"redacted": True},
                )
            )
            await unit.session.flush()
            unit.session.add(
                ContextRevisionRecord(
                    id=revision_id,
                    run_id=run_id,
                    revision=await unit.runs.allocate_context_revision(run_id),
                    parent_id=state.context_revision_id,
                    dedupe_key=dedupe,
                    input_hash=input_hash,
                    policy_hash=policy_hash,
                    artifact_id=artifact_id,
                    summary=summary,
                    estimate={"before": before, "after": after},
                )
            )
            await unit.session.flush()
            event = await unit.events.append(
                run_id=run_id,
                event_type="context.summarized",
                payload={"revision_id": str(revision_id), "before": before, "after": after},
                created_at=utc_now(),
            )
            unit.snapshots.add(
                RunSnapshotRecord(
                    run_id=run_id,
                    event_sequence=event.sequence,
                    state=revised.model_dump(mode="json"),
                    schema_version=2,
                )
            )
            await unit.commit()
        return revised

    async def _degraded(self, reason):
        async with UnitOfWork(self.factory) as unit:
            await self.guard.check(unit.session)
            await unit.events.append(
                run_id=self.guard.lease.run_id,
                event_type="context.summary_degraded",
                payload={"reason": reason, "helper_tokens": 0},
                created_at=utc_now(),
            )
            await unit.commit()

    async def verify_restored_messages(self, state):
        """Check derived messages even without a compaction artifact; never rewrite goals."""
        derived = [
            {"role": message.role.value, "content": message.content}
            for message in state.messages
            if message.role is MessageRole.TOOL
            or (
                message.role is MessageRole.USER
                and message.context_priority == 0
                and (message.content or "").startswith("以下仅为低可信历史摘录")
            )
        ]
        if not derived:
            return
        body = json.dumps(derived, ensure_ascii=False, sort_keys=True)
        try:
            await self.injection.verify_derived_text(
                text=body,
                run_id=self.guard.lease.run_id,
                source_id="snapshot:derived_messages",
                source_hash=text_hash(body),
                purpose="snapshot_restore",
            )
        except ToolError as error:
            raise ContextPolicyError(
                "context_source_blocked", "restored derived messages require new safe context"
            ) from error
