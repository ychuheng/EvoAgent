"""消息序号在数据库内分配，调用者负责提交聚合事务。"""

import hashlib
import json
from datetime import UTC, datetime

from sqlalchemy import select, update

from evoagent.db.models import MessageRecord, SessionRecord

# I-03：运行中补充的指令与任务目标分开记账。
INSTRUCTION_KIND = "instruction"


def text_hash(content: str) -> str:
    return "sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest()


async def append_message(session, *, task, run, kind: str, role: str, content: str):
    # 锁住会话后检查幂等键；不能以 max(sequence)+1 分配。
    sequence = await session.scalar(
        update(SessionRecord)
        .where(SessionRecord.id == task.session_id)
        .values(next_message_sequence=SessionRecord.next_message_sequence + 1)
        .returning(SessionRecord.next_message_sequence)
    )
    if sequence is None:
        raise ValueError("session does not exist")
    existing = await session.scalar(
        select(MessageRecord).where(MessageRecord.run_id == run.id, MessageRecord.kind == kind)
    )
    if existing is not None:
        return existing
    record = MessageRecord(
        session_id=task.session_id,
        task_id=task.id,
        run_id=run.id,
        session_sequence=sequence - 1,
        kind=kind,
        role=role,
        content=content,
        content_hash=text_hash(content),
        backfill=False,
    )
    session.add(record)
    await session.flush()
    return record


async def project_terminal(session, task, run):
    if run.status.value not in {"completed", "failed", "cancelled", "timeout", "limit_reached"}:
        return
    content = run.final_answer if run.status.value == "completed" else None
    await append_message(
        session,
        task=task,
        run=run,
        kind="terminal",
        role="assistant",
        content=content or json.dumps({"status": run.status.value, "error_code": run.error_code}),
    )


async def history_for_task(session, task):
    return tuple(
        await session.scalars(
            select(MessageRecord)
            .where(
                MessageRecord.session_id == task.session_id,
                MessageRecord.session_sequence < task.history_before_sequence,
            )
            .order_by(MessageRecord.session_sequence)
        )
    )


async def record_instruction(session, *, task, content: str) -> MessageRecord:
    """记录一条运行中补充的指令。

    它**只**是一条待注入的消息：不影响已经在执行或等待审批的动作，也不改变任务目标。
    """

    normalized = content.strip()
    if not normalized:
        raise ValueError("instruction cannot be blank")
    sequence = await session.scalar(
        update(SessionRecord)
        .where(SessionRecord.id == task.session_id)
        .values(next_message_sequence=SessionRecord.next_message_sequence + 1)
        .returning(SessionRecord.next_message_sequence)
    )
    if sequence is None:
        raise ValueError("session does not exist")
    record = MessageRecord(
        session_id=task.session_id,
        task_id=task.id,
        run_id=None,
        session_sequence=sequence - 1,
        kind=INSTRUCTION_KIND,
        role="user",
        content=normalized,
        content_hash=text_hash(normalized),
        backfill=False,
        injected_at=None,
    )
    session.add(record)
    await session.flush()
    return record


async def claim_pending_instructions(session, *, task_id) -> tuple[str, ...]:
    """取走该 Task 尚未注入的指令并标记注入时间。

    返回内容按序号排序；同一事务内标记 `injected_at`，因此重放不会重复注入。
    """

    records = tuple(
        await session.scalars(
            select(MessageRecord)
            .where(
                MessageRecord.task_id == task_id,
                MessageRecord.kind == INSTRUCTION_KIND,
                MessageRecord.injected_at.is_(None),
            )
            .order_by(MessageRecord.session_sequence)
        )
    )
    if not records:
        return ()
    now = datetime.now(UTC)
    for record in records:
        record.injected_at = now
    await session.flush()
    return tuple(record.content for record in records)
