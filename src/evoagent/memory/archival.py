"""归档任务使用固定消息范围；读接口绝不生成摘要。"""

import json

from sqlalchemy import select

from evoagent.db.models import (
    MaintenanceJobRecord,
    MemorySourceRecord,
    MemoryVersionRecord,
    MessageRecord,
    SessionRecord,
)
from evoagent.memory.policy import redact
from evoagent.memory.schema import MemoryError
from evoagent.sessions.service import text_hash


async def archive_input(session, session_id, end_sequence):
    messages = tuple(
        await session.scalars(
            select(MessageRecord)
            .where(
                MessageRecord.session_id == session_id,
                MessageRecord.session_sequence <= end_sequence,
            )
            .order_by(MessageRecord.session_sequence)
        )
    )
    if len(messages) > 1000 or sum(len(m.content) for m in messages) > 1_000_000:
        raise MemoryError("archive_input_limit")
    for message in messages:
        if text_hash(message.content) != message.content_hash:
            raise MemoryError("archive_source_changed")
    erased = await session.scalar(
        select(MemorySourceRecord.id)
        .join(MemoryVersionRecord, MemorySourceRecord.version_id == MemoryVersionRecord.id)
        .where(
            MemorySourceRecord.message_id.in_([m.id for m in messages]),
            MemoryVersionRecord.status == "erased",
        )
    )
    if erased:
        raise MemoryError("archive_contains_erased_source")
    source_hash = text_hash(
        json.dumps(
            [(str(m.id), m.session_sequence, m.content_hash) for m in messages],
            separators=(",", ":"),
        )
    )
    return messages, source_hash


async def enqueue_archive(factory, session_id):
    async with factory() as session:
        scope = await session.scalar(
            select(SessionRecord).where(SessionRecord.id == session_id).with_for_update()
        )
        if scope is None:
            raise MemoryError("session_not_found")
        end = scope.next_message_sequence - 1
        messages, digest = await archive_input(session, session_id, end)
        if not messages:
            raise MemoryError("archive_empty")
        key = f"archive:{session_id}:{end}:{digest}"
        job = await session.scalar(
            select(MaintenanceJobRecord).where(MaintenanceJobRecord.dedupe_key == key)
        )
        if job is None:
            job = MaintenanceJobRecord(
                dedupe_key=key,
                kind="archive",
                payload={
                    "session_id": str(session_id),
                    "end_sequence": end,
                    "source_hash": digest,
                    "config": {"method": "extractive-v1", "max_chars": 16000},
                },
            )
            session.add(job)
        await session.commit()
        return job


def summarize_archive(messages):
    # 归档只是可追溯的低可信摘录，不写入 MemoryVersion。
    return "\n".join(
        f"[{m.session_sequence}:{m.role}] {redact(m.content)[:256]}" for m in messages
    )[:16000]
