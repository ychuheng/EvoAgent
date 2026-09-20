"""Embedding 独立契约：固定身份、批次原子校验，不吞掉取消。"""

import hashlib
import math
from dataclasses import dataclass
from typing import Protocol

import httpx

DIMENSION = 1536


class EmbeddingError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class EmbeddingProfile:
    model: str
    dimension: int = DIMENSION
    preprocessing: str = "text-v1"
    metric: str = "cosine"


@dataclass(frozen=True)
class EmbeddingResult:
    vectors: tuple[tuple[float, ...], ...]
    model: str
    usage: int | None = None


def validate(result, profile, count):
    if (
        profile.dimension != DIMENSION
        or profile.metric != "cosine"
        or profile.preprocessing != "text-v1"
    ):
        raise EmbeddingError("embedding profile unsupported")
    if result.model != profile.model or len(result.vectors) != count:
        raise EmbeddingError("embedding identity/count mismatch")
    for vector in result.vectors:
        if (
            len(vector) != profile.dimension
            or any(
                isinstance(v, bool)
                or not isinstance(v, (int, float))
                or not math.isfinite(v)
                or abs(v) > 3.4028234e38
                for v in vector
            )
            or not any(abs(v) >= 1.1754944e-38 for v in vector)
        ):
            raise EmbeddingError("embedding dimension/value mismatch")
    return result


def validate_input(texts):
    if not 1 <= len(texts) <= 16 or any(not t.strip() or len(t) > 12000 for t in texts):
        raise EmbeddingError("embedding input limit")


class EmbeddingProvider(Protocol):
    async def embed(self, texts: tuple[str, ...], profile: EmbeddingProfile) -> EmbeddingResult: ...


class MockEmbeddingProvider:
    """确定性特征哈希只用于协议/索引测试，不冒充语义模型。"""

    async def embed(self, texts, profile):
        validate_input(texts)
        if profile.model != "mock-hash-v1":
            raise EmbeddingError("mock profile mismatch")
        from evoagent.retrieval.lexical import tokenize

        vectors = []
        for text in texts:
            vector = [0.0] * profile.dimension
            for term in tokenize(text) or (text,):
                digest = hashlib.sha256(term.encode()).digest()
                vector[int.from_bytes(digest[:4], "big") % profile.dimension] += 1.0
            vectors.append(tuple(vector))
        return validate(EmbeddingResult(tuple(vectors), profile.model, 0), profile, len(texts))


class OpenAIEmbeddingProvider:
    def __init__(self, *, api_key: str, base_url: str, client=None):
        self.client = client or httpx.AsyncClient(timeout=15)
        self.owns_client = client is None
        self.key = api_key
        self.url = base_url.rstrip("/") + "/embeddings"

    async def embed(self, texts, profile):
        validate_input(texts)
        try:
            response = await self.client.post(
                self.url,
                headers={"Authorization": f"Bearer {self.key}"},
                json={
                    "model": profile.model,
                    "input": list(texts),
                    "dimensions": profile.dimension,
                    "encoding_format": "float",
                },
            )
            response.raise_for_status()
            body = response.json()
            rows = sorted(body["data"], key=lambda r: r["index"])
            if [r["index"] for r in rows] != list(range(len(texts))):
                raise EmbeddingError("partial embedding batch")
            result = EmbeddingResult(
                tuple(tuple(r["embedding"]) for r in rows),
                body["model"],
                body.get("usage", {}).get("total_tokens"),
            )
            return validate(result, profile, len(texts))
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as error:
            raise EmbeddingError("embedding provider failed validation or transport") from error

    async def aclose(self):
        if self.owns_client:
            await self.client.aclose()


def provider_from_settings(settings):
    if settings.embedding_model == "mock-hash-v1":
        return MockEmbeddingProvider()
    if not settings.embedding_api_key or not settings.embedding_base_url:
        raise EmbeddingError("embedding connection not configured")
    return OpenAIEmbeddingProvider(
        api_key=settings.embedding_api_key.get_secret_value(),
        base_url=str(settings.embedding_base_url),
    )
