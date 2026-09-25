"""Compare frozen Chinese paraphrases with the actual local embedding service.

Run against the acceptance sidecar; no user messages or credentials are stored.
"""

import argparse
import asyncio
import json
from pathlib import Path
from time import perf_counter

from evoagent.retrieval.embeddings import (
    EmbeddingProfile,
    EmbeddingResult,
    OpenAIEmbeddingProvider,
)
from evoagent.retrieval.hybrid import rank
from evoagent.retrieval.lexical import bm25
from evoagent.retrieval.vector import cosine_distance


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", default="evals/datasets/phase4-semantic-retrieval-v1.json")
    parser.add_argument("--base-url", default="http://127.0.0.1:18100/v1")
    parser.add_argument("--max-distance", type=float, default=0.35)
    parser.add_argument("--database-url")
    parser.add_argument("--output")
    args = parser.parse_args()
    dataset = json.loads(Path(args.dataset).read_text(encoding="utf-8"))
    documents = {item["fact_key"]: item["text"] for item in dataset["documents"]}
    queries = dataset["queries"]
    profile = EmbeddingProfile(
        model="sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
        dimension=384,
        preprocessing="fastembed-0.7.4-mean-v1",
    )
    provider = OpenAIEmbeddingProvider(api_key="local-acceptance", base_url=args.base_url)
    started = perf_counter()
    try:
        texts = tuple(documents.values()) + tuple(row["text"] for row in queries)
        batches = [
            await provider.embed(texts[i : i + 16], profile) for i in range(0, len(texts), 16)
        ]
        result = EmbeddingResult(
            tuple(vector for batch in batches for vector in batch.vectors),
            profile.model,
            sum(batch.usage or 0 for batch in batches),
        )
    finally:
        await provider.aclose()
    elapsed_ms = round((perf_counter() - started) * 1000)
    keys = tuple(documents)
    rows = []
    for offset, query in enumerate(queries):
        lexical = bm25(query["text"], documents)
        # The production lexical route excludes scores below 0.1.
        lexical_hit = next((key for key, score, _ in lexical if score >= 0.1), None)
        distances = {
            key: cosine_distance(result.vectors[i], result.vectors[len(keys) + offset])
            for i, key in enumerate(keys)
        }
        combined = rank(query["text"], documents, distances, maximum_distance=args.max_distance)
        rows.append(
            {
                "expected": query["expected_fact_key"],
                "lexical_top1": lexical_hit,
                "hybrid_top1": combined[0][0] if combined else None,
                "vector_top1": min(distances, key=distances.get),
                "expected_distance": round(distances[query["expected_fact_key"]], 5),
                "hybrid_candidates": len(combined),
            }
        )
    report = {
        "dataset": dataset["name"],
        "version": dataset["version"],
        "profile": {
            "model": profile.model,
            "dimension": profile.dimension,
            "preprocessing": profile.preprocessing,
        },
        "samples": len(rows),
        "maximum_distance": args.max_distance,
        "lexical_top1_hits": sum(row["lexical_top1"] == row["expected"] for row in rows),
        "hybrid_top1_hits": sum(row["hybrid_top1"] == row["expected"] for row in rows),
        "vector_top1_hits": sum(row["vector_top1"] == row["expected"] for row in rows),
        "embedding_batch_ms": elapsed_ms,
        "input_characters": sum(map(len, documents.values()))
        + sum(len(row["text"]) for row in queries),
        "provider_usage_tokens": result.usage,
        "cases": rows,
    }
    if args.database_url:
        report["postgres_index"] = await verify_postgres(
            args.database_url, dataset, profile, args.base_url
        )
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


async def verify_postgres(url, dataset, profile, base_url):
    """Persist actual memory sources and query pgvector in a dedicated migrated DB."""
    from sqlalchemy import func, select

    from evoagent.db.models import (
        DocumentEmbeddingRecord,
        MessageRecord,
        RetrievalDocumentRecord,
        RunRecord,
    )
    from evoagent.db.session import Database
    from evoagent.memory.maintenance import MaintenanceWorker
    from evoagent.memory.schema import MemoryDecision, MemoryProposal
    from evoagent.memory.service import MemoryService
    from evoagent.retrieval.indexing import IndexService
    from evoagent.retrieval.vector import exact_distances
    from evoagent.tasks.lease import JobLeaseManager, TaskExecutionResult
    from evoagent.tasks.service import TaskService
    from evoagent.tasks.state_machine import PersistentRunStatus
    from evoagent.trace.artifacts import LocalArtifactStore

    db = Database(url)
    embedder = OpenAIEmbeddingProvider(api_key="local-acceptance", base_url=base_url)
    try:
        async with db.session_factory() as transaction:
            if await transaction.scalar(select(func.count()).select_from(RunRecord)):
                raise ValueError("semantic acceptance requires an empty dedicated Run database")
        tasks = TaskService(db.session_factory)
        session = await tasks.create_session("semantic acceptance isolated")
        memory = MemoryService(db.session_factory)
        source_for_key = {}
        for item in dataset["documents"]:
            task = await tasks.create_task(
                session_id=session.id,
                goal=item["text"],
                provider="mock",
                model="acceptance-source",
            )
            manager = JobLeaseManager(db.session_factory, lease_seconds=60)
            lease = await manager.claim_next("semantic-acceptance")
            assert lease.run_id == task.run.id
            await manager.finalize(
                lease,
                TaskExecutionResult(status=PersistentRunStatus.COMPLETED, final_answer="已记录"),
            )
            async with db.session_factory() as transaction:
                message = await transaction.scalar(
                    select(MessageRecord).where(
                        MessageRecord.run_id == task.run.id,
                        MessageRecord.kind == "goal",
                    )
                )
            entry, version = await memory.propose(
                session.id,
                MemoryProposal(
                    source_message_id=message.id,
                    fact_key=item["fact_key"],
                    content=item["text"],
                    kind="fact",
                    scope="workspace",
                ),
            )
            await memory.decide(
                session.id,
                version.id,
                MemoryDecision(action="confirm", expected_lock_version=entry.lock_version),
            )
            source_for_key[item["fact_key"]] = f"memory:{version.id}"
        index = IndexService(
            db.session_factory,
            embedder,
            model=profile.model,
            dimension=profile.dimension,
            preprocessing=profile.preprocessing,
        )
        maintenance = MaintenanceWorker(
            db.session_factory, LocalArtifactStore(Path("workspace/semantic-acceptance")), index
        )
        processed = 0
        while await maintenance.run_once():
            processed += 1
            assert processed <= 64
        active = await index.ensure_profile()
        async with db.session_factory() as transaction:
            documents = list(
                await transaction.scalars(
                    select(RetrievalDocumentRecord).where(
                        RetrievalDocumentRecord.source_key.in_(source_for_key.values())
                    )
                )
            )
            embeddings = list(
                await transaction.scalars(
                    select(DocumentEmbeddingRecord).where(
                        DocumentEmbeddingRecord.profile_id == active.id
                    )
                )
            )
            allowed = {item.id for item in documents}
            query = await embedder.embed((dataset["queries"][0]["text"],), profile)
            distances = await exact_distances(
                transaction,
                profile_id=active.id,
                generation=active.active_generation,
                allowed_documents=allowed,
                vector=query.vectors[0],
            )
        assert len(documents) == len(embeddings) == len(dataset["documents"])
        assert all(len(item.vector) == profile.dimension for item in embeddings)
        assert set(distances) == allowed
        return {
            "database": "dedicated migrated PostgreSQL",
            "indexed_sources": len(documents),
            "native_dimension": profile.dimension,
            "queried_vectors": len(distances),
            "processed_maintenance_jobs": processed,
        }
    finally:
        await embedder.aclose()
        await db.dispose()


if __name__ == "__main__":
    asyncio.run(main())
