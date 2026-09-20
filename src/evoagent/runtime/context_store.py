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
from evoagent.db.models import ArtifactRecord, ContextRevisionRecord, RunSnapshotRecord, utc_now
from evoagent.db.unit_of_work import UnitOfWork
from evoagent.memory.policy import redact_value
from evoagent.memory.repository import check_run_references
from evoagent.memory.schema import MemoryError
from evoagent.memory.summarization import ExtractiveSummarizer
from evoagent.sessions.service import text_hash


class ContextStore:
    def __init__(self, factory, guard, artifact_store, policy, *, history_before_sequence=0):
        self.factory = factory
        self.guard = guard
        self.store = artifact_store
        self.policy = policy
        self.history_before_sequence = history_before_sequence
        self.summarizer = ExtractiveSummarizer()

    async def check_sources(self):
        async with self.factory() as session:
            await self.guard.check(session)
            try:
                await check_run_references(session, self.guard.lease.run_id)
            except MemoryError as error:
                raise ContextPolicyError("context_source_revoked", str(error)) from error

    async def prepare(self, state, request):
        await self.check_sources()
        if state.context_revision_id:
            async with self.factory() as session:
                revision = await session.get(ContextRevisionRecord, state.context_revision_id)
                if revision is None or revision.run_id != self.guard.lease.run_id:
                    raise ContextPolicyError("context_revision_invalid", "revision unavailable")
                artifact = await session.get(ArtifactRecord, revision.artifact_id)
                try:
                    data = await self.store.read(artifact.uri)
                    valid = text_hash(data.decode("utf-8")) == artifact.content_hash
                except (OSError, ValueError):
                    valid = False
                if not valid:
                    raise ContextPolicyError(
                        "context_artifact_invalid", "context artifact is corrupt"
                    )
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
                    revision=latest.revision + 1 if latest else 1,
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
