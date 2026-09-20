"""先阈值后 RRF；只在调用者已经授权的候选集合内排名。"""

from evoagent.retrieval.lexical import bm25

ROUTE_TOP_N = 100


def rank(query, documents, distances, *, minimum_score=0.1, maximum_distance=0.35, rrf_k=60):
    lexical = [row for row in bm25(query, documents) if row[1] >= minimum_score][:ROUTE_TOP_N]
    vector = sorted(
        (
            (key, value)
            for key, value in distances.items()
            if key in documents and value <= maximum_distance
        ),
        key=lambda row: (row[1], str(row[0])),
    )[:ROUTE_TOP_N]
    evidence = {}
    for position, (key, score, terms) in enumerate(lexical, 1):
        evidence[key] = {
            "lexical_rank": position,
            "lexical_score": score,
            "terms": list(terms),
            "vector_rank": None,
            "distance": None,
            "rrf": 1 / (rrf_k + position),
        }
    for position, (key, distance) in enumerate(vector, 1):
        row = evidence.setdefault(
            key, {"lexical_rank": None, "lexical_score": 0, "terms": [], "rrf": 0}
        )
        row.update(vector_rank=position, distance=distance)
        row["rrf"] += 1 / (rrf_k + position)
    return tuple(sorted(evidence.items(), key=lambda row: (-row[1]["rrf"], str(row[0]))))
