"""本地可信 API：作用域由 Session 解析，模型工具无确认入口。"""

from uuid import UUID

from fastapi import APIRouter, Query, Request
from sqlalchemy import select

from evoagent.api.dependencies import DatabaseDependency
from evoagent.db.models import (
    MaintenanceJobRecord,
    MemoryEventRecord,
    MemorySourceRecord,
    MessageRecord,
    SessionArchiveRecord,
    SessionRecord,
)
from evoagent.memory.archival import enqueue_archive
from evoagent.memory.extraction import extract_proposals
from evoagent.memory.repository import source_message
from evoagent.memory.schema import MemoryDecision, MemoryError, MemoryProposal
from evoagent.memory.service import MemoryService

router = APIRouter(tags=["memory"])


def memory_view(entry, version):
    return {
        "entry_id": entry.id,
        "version_id": version.id,
        "fact_key": entry.fact_key,
        "scope": entry.scope_key,
        "lock_version": entry.lock_version,
        "revision": version.revision,
        "kind": version.kind,
        "status": version.status,
        "content": version.content,
        "content_hash": version.content_hash,
        "confidence": version.confidence,
        "confidence_method": version.confidence_method,
        "expires_at": version.expires_at,
    }


@router.get("/sessions/{session_id}/messages")
async def messages(
    session_id: UUID, database: DatabaseDependency, before: int | None = Query(default=None, ge=1)
):
    async with database.session_factory() as session:
        if await session.get(SessionRecord, session_id) is None:
            raise MemoryError("session_not_found")
        query = select(MessageRecord).where(MessageRecord.session_id == session_id)
        if before is not None:
            query = query.where(MessageRecord.session_sequence < before)
        rows = await session.scalars(query.order_by(MessageRecord.session_sequence).limit(1000))
        return [
            {
                "id": m.id,
                "sequence": m.session_sequence,
                "role": m.role,
                "content": m.content,
                "hash": m.content_hash,
                "backfill": m.backfill,
                "run_id": m.run_id,
                "task_id": m.task_id,
                "kind": m.kind,
                "created_at": m.created_at,
            }
            for m in rows
        ]


@router.post("/sessions/{session_id}/memories", status_code=201)
async def propose(session_id: UUID, body: MemoryProposal, database: DatabaseDependency):
    return memory_view(*await MemoryService(database.session_factory).propose(session_id, body))


@router.get("/sessions/{session_id}/memories")
async def memories(
    session_id: UUID,
    database: DatabaseDependency,
    query: str | None = Query(default=None, max_length=1000),
):
    return [
        memory_view(*row)
        for row in await MemoryService(database.session_factory).list(session_id, query=query)
    ]


@router.post("/sessions/{session_id}/memories/{version_id}/decision")
async def decide(
    session_id: UUID, version_id: UUID, body: MemoryDecision, database: DatabaseDependency
):
    result = memory_view(
        *await MemoryService(database.session_factory).decide(session_id, version_id, body)
    )
    if body.action == "erase":
        async with database.session_factory() as session:
            result["maintenance_job_id"] = await session.scalar(
                select(MaintenanceJobRecord.id).where(
                    MaintenanceJobRecord.dedupe_key == f"erase:{version_id}"
                )
            )
    return result


@router.get("/sessions/{session_id}/memories/{version_id}")
async def memory_detail(session_id: UUID, version_id: UUID, database: DatabaseDependency):
    rows = await MemoryService(database.session_factory).list(session_id)
    selected = next((row for row in rows if row[1].id == version_id), None)
    if selected is None:
        raise MemoryError("memory_not_found")
    result = memory_view(*selected)
    async with database.session_factory() as session:
        result["sources"] = [
            {
                "message_id": source.message_id,
                "source_hash": source.source_hash,
                "locator": source.locator,
            }
            for source in await session.scalars(
                select(MemorySourceRecord).where(MemorySourceRecord.version_id == version_id)
            )
        ]
        result["events"] = [
            {
                "action": event.action,
                "actor": event.actor,
                "reason": event.reason,
                "created_at": event.created_at,
            }
            for event in await session.scalars(
                select(MemoryEventRecord)
                .where(MemoryEventRecord.version_id == version_id)
                .order_by(MemoryEventRecord.created_at)
            )
        ]
        result["maintenance_job_id"] = await session.scalar(
            select(MaintenanceJobRecord.id).where(
                MaintenanceJobRecord.dedupe_key == f"erase:{version_id}"
            )
        )
    return result


@router.post("/sessions/{session_id}/memory-extractions/{message_id}")
async def extract(
    session_id: UUID, message_id: UUID, database: DatabaseDependency, request: Request
):
    async with database.session_factory() as session:
        message = await source_message(session, message_id, session_id)
        generator = request.app.state.memory_generator
        proposals = extract_proposals(message) if generator is None else ()
    if generator is not None:
        proposals = await generator.generate(message)
    service = MemoryService(database.session_factory)
    return [
        memory_view(
            *await service.propose(
                session_id, p, origin="model-v1" if generator else "extractive-v1"
            )
        )
        for p in proposals
    ]


@router.post("/sessions/{session_id}/archives", status_code=202)
async def archive(session_id: UUID, database: DatabaseDependency):
    job = await enqueue_archive(database.session_factory, session_id)
    return {"job_id": job.id, "status": job.status}


@router.get("/sessions/{session_id}/archives")
async def archives(session_id: UUID, database: DatabaseDependency):
    async with database.session_factory() as session:
        rows = await session.scalars(
            select(SessionArchiveRecord)
            .where(SessionArchiveRecord.session_id == session_id)
            .order_by(SessionArchiveRecord.created_at)
        )
        return [
            {
                "id": r.id,
                "start_sequence": r.start_sequence,
                "end_sequence": r.end_sequence,
                "summary": r.summary,
                "source_hash": r.source_hash,
                "status": r.status,
            }
            for r in rows
        ]


@router.get("/maintenance-jobs/{job_id}")
async def job_status(job_id: UUID, database: DatabaseDependency):
    async with database.session_factory() as session:
        job = await session.get(MaintenanceJobRecord, job_id)
        if job is None:
            raise MemoryError("maintenance_job_not_found")
        return {
            "id": job.id,
            "kind": job.kind,
            "status": job.status,
            "attempts": job.attempts,
            "result": job.result,
            "error_code": job.error_code,
            "next_attempt_at": job.next_attempt_at,
        }


@router.post("/maintenance-jobs/{job_id}/retry", status_code=202)
async def retry_job(job_id: UUID, database: DatabaseDependency):
    async with database.session_factory() as session:
        job = await session.scalar(
            select(MaintenanceJobRecord).where(MaintenanceJobRecord.id == job_id).with_for_update()
        )
        if job is None or job.status != "failed":
            raise MemoryError("maintenance_retry_conflict")
        job.status = "pending"
        job.error_code = None
        job.next_attempt_at = None
        await session.commit()
        return {"job_id": job.id, "status": job.status}
