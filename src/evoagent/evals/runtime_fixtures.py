"""仅由实验创建器写入的公开、固定记忆 fixture；来源与实验执行记录分离。"""

from datetime import UTC, datetime, timedelta

from evoagent.db.models import (
    MemoryEntryRecord,
    MemorySourceRecord,
    MemoryVersionRecord,
    RunRecord,
    SessionRecord,
    TaskRecord,
    WorkspaceRecord,
)
from evoagent.retrieval.indexing import enqueue_source
from evoagent.sessions.service import append_message, text_hash
from evoagent.tasks.state_machine import PersistentRunStatus, TaskStatus


async def seed_memories(db, target, fixtures):
    for index, fixture in enumerate(fixtures):
        content = fixture["content"]
        workspace_id = target.workspace_id
        if fixture.get("scope", "workspace") == "foreign":
            foreign = WorkspaceRecord(name="isolated-foreign-fixture")
            db.add(foreign)
            await db.flush()
            workspace_id = foreign.id
        origin = SessionRecord(title="public-memory-fixture", workspace_id=workspace_id)
        db.add(origin)
        await db.flush()
        task = TaskRecord(session_id=origin.id, goal=content, status=TaskStatus.COMPLETED)
        db.add(task)
        await db.flush()
        run = RunRecord(
            task_id=task.id,
            provider="fixture",
            model="fixed-public-source",
            status=PersistentRunStatus.COMPLETED,
            run_mode="baseline",
        )
        db.add(run)
        await db.flush()
        source = await append_message(
            db, task=task, run=run, kind="goal", role="user", content=content
        )
        status = fixture.get("status", "confirmed")
        if status not in {"confirmed", "revoked"}:
            raise ValueError("invalid fixture memory status")
        entry = MemoryEntryRecord(
            workspace_id=workspace_id,
            scope_key="workspace",
            fact_key=fixture.get("key", f"fixture-{index}"),
            status=status,
        )
        db.add(entry)
        await db.flush()
        version = MemoryVersionRecord(
            entry_id=entry.id,
            revision=1,
            kind="fact",
            content=content,
            content_hash=text_hash(content),
            status=status,
            confidence=1,
            confidence_method="fixed-public-fixture",
            origin_type="manual",
            expires_at=datetime.now(UTC) - timedelta(days=1) if fixture.get("expired") else None,
        )
        db.add(version)
        await db.flush()
        entry.current_version_id = version.id
        db.add(
            MemorySourceRecord(
                version_id=version.id,
                message_id=source.id,
                source_hash=source.content_hash,
                locator=f"fixture:{index}",
            )
        )
        await enqueue_source(db, f"memory:{version.id}")
