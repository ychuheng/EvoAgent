"""报告取证：保留实际选择、降级、摘要和已知用量，不推算未报告的成本。"""

from sqlalchemy import select

from evoagent.db.models import (
    ContextRevisionRecord,
    EvalCaseRecord,
    RetrievalBatchRecord,
    RetrievalSelectionRecord,
    RunEventRecord,
    TurnRecord,
)
from evoagent.evals.metrics import model_usage_rows
from evoagent.evals.runtime_schema import retrieval_metrics


async def evidence_for_run(db, record):
    turns = tuple(await db.scalars(select(TurnRecord).where(TurnRecord.run_id == record.run_id)))
    revisions = tuple(
        await db.scalars(
            select(ContextRevisionRecord).where(ContextRevisionRecord.run_id == record.run_id)
        )
    )
    batches = tuple(
        await db.scalars(
            select(RetrievalBatchRecord).where(RetrievalBatchRecord.run_id == record.run_id)
        )
    )
    selections = tuple(
        await db.scalars(
            select(RetrievalSelectionRecord)
            .where(
                RetrievalSelectionRecord.batch_id.in_([batch.id for batch in batches]),
                RetrievalSelectionRecord.omission_reason.is_(None),
            )
            .order_by(RetrievalSelectionRecord.rank)
        )
    )
    events = tuple(
        await db.scalars(
            select(RunEventRecord)
            .where(
                RunEventRecord.run_id == record.run_id,
                RunEventRecord.event_type.in_(
                    [
                        "context.checked",
                        "context.trimmed",
                        "worker.claimed",
                        "retrieval.frozen",
                        "retrieval.degraded",
                        "model.completed",
                        "model.failed",
                    ]
                ),
            )
            .order_by(RunEventRecord.sequence)
        )
    )
    case = await db.get(EvalCaseRecord, record.case_id)
    relevant = case.risk_profile.get("relevant_source_hashes")
    embedding = [
        event.payload.get("embedding_tokens")
        for event in events
        if event.event_type.startswith("retrieval.")
    ]
    usage_rows = model_usage_rows(turns, events)
    return {
        "known_main_tokens": sum((usage or {}).get("total_tokens", 0) for usage in usage_rows),
        "unknown_main_calls": sum(usage is None for usage in usage_rows),
        "embedding_tokens": sum(embedding)
        if all(value is not None for value in embedding)
        else None,
        "summaries": [
            {"id": str(row.id), "estimate": row.estimate, "input_hash": row.input_hash}
            for row in revisions
        ],
        "retrieval": {
            "batches": len(batches),
            "degraded": [row.degraded for row in batches if row.degraded],
            "selected_hashes": [row.source_hash for row in selections],
            "quality": retrieval_metrics([row.source_hash for row in selections], relevant, 10)
            if relevant is not None
            else None,
        },
        "events": [{"type": event.event_type, "payload": event.payload} for event in events],
    }
