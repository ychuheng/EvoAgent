"""复用阶段三分词规则的通用 BM25，身份用于稳定排序。"""

import math
import re
from collections import Counter


def tokenize(text):
    lowered = text.lower()
    terms = re.findall(r"[a-zA-Z0-9_]+", lowered)
    for run in re.findall(r"[\u3400-\u9fff]+", lowered):
        terms.extend(run)
        terms.extend(run[i : i + 2] for i in range(len(run) - 1))
    return tuple(terms)


def bm25(query, documents, *, k1=1.5, b=0.75):
    """documents 为 id → text，返回 id/score/matched_terms。"""
    if not documents:
        return ()
    terms = tuple(dict.fromkeys(tokenize(query)))
    tokens = {key: tokenize(text) for key, text in documents.items()}
    average = sum(map(len, tokens.values())) / len(tokens) or 1
    df = Counter(t for row in tokens.values() for t in set(row) if t in terms)
    rows = []
    for key, row in tokens.items():
        counts = Counter(row)
        matched = tuple(t for t in terms if counts[t])
        score = sum(
            math.log(1 + (len(tokens) - df[t] + 0.5) / (df[t] + 0.5))
            * counts[t]
            * (k1 + 1)
            / (counts[t] + k1 * (1 - b + b * len(row) / average))
            for t in matched
        )
        rows.append((key, score, matched))
    return tuple(sorted(rows, key=lambda row: (-row[1], str(row[0]))))
