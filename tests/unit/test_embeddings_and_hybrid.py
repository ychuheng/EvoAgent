import asyncio
import math

import httpx
import pytest

from evoagent.retrieval.embeddings import (
    DIMENSION,
    EmbeddingError,
    EmbeddingProfile,
    EmbeddingResult,
    MockEmbeddingProvider,
    OpenAIEmbeddingProvider,
    validate,
)
from evoagent.retrieval.hybrid import rank
from evoagent.retrieval.lexical import bm25


@pytest.mark.parametrize(
    "vector",
    [
        (0.0,) * DIMENSION,
        (1.0,),
        (math.nan,) * DIMENSION,
        (math.inf,) * DIMENSION,
        (True,) * DIMENSION,
        (1e100,) * DIMENSION,
        (1e-100,) * DIMENSION,
    ],
)
def test_embedding_invalid_vectors_fail(vector):
    with pytest.raises(EmbeddingError):
        validate(EmbeddingResult((vector,), "model"), EmbeddingProfile("model"), 1)


def test_same_dimension_different_model_is_not_compatible():
    with pytest.raises(EmbeddingError):
        validate(EmbeddingResult(((1.0,) * DIMENSION,), "other"), EmbeddingProfile("model"), 1)


async def test_mock_deterministic_and_bounded():
    provider = MockEmbeddingProvider()
    profile = EmbeddingProfile("mock-hash-v1")
    assert await provider.embed(("中文 English",), profile) == await provider.embed(
        ("中文 English",), profile
    )
    for batch in ((), ("",), ("x" * 12001,), ("text",) * 17):
        with pytest.raises(EmbeddingError):
            await provider.embed(batch, profile)


@pytest.mark.parametrize("mode", ["partial", "duplicate", "wrong_model", "valid", "cancel"])
async def test_http_provider_validates_whole_batch_and_propagates_cancel(mode):
    def response(request):
        import json

        body = json.loads(request.content)
        assert body["dimensions"] == DIMENSION and body["encoding_format"] == "float"
        if mode == "cancel":
            raise asyncio.CancelledError()
        data = [{"index": 0, "embedding": [1.0] * DIMENSION}]
        if mode != "partial":
            data.append({"index": 0 if mode == "duplicate" else 1, "embedding": [1.0] * DIMENSION})
        return httpx.Response(
            200,
            json={
                "model": "bad" if mode == "wrong_model" else "text-embedding-3-small",
                "data": data,
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
        provider = OpenAIEmbeddingProvider(
            api_key="test", base_url="https://embedding.test/v1", client=client
        )
        profile = EmbeddingProfile("text-embedding-3-small")
        if mode == "valid":
            result = await provider.embed(("one", "two"), profile)
            assert result.usage is None and len(result.vectors) == 2
        else:
            with pytest.raises(asyncio.CancelledError if mode == "cancel" else EmbeddingError):
                await provider.embed(("one", "two"), profile)


def test_rrf_accepts_semantic_hit_but_never_outside_allowed_set():
    documents = {"allowed": "automobile repair", "noise": "garden"}
    ranked = rank("car", documents, {"allowed": 0.1, "noise": 0.9, "forbidden": 0.0})
    assert [key for key, _ in ranked] == ["allowed"]
    assert ranked[0][1]["lexical_rank"] is None
    assert rank("unrelated", documents, {"noise": 0.9}) == ()


def test_lexical_stable_ties_and_chinese_tokenization():
    assert [key for key, _, _ in bm25("中文", {"b": "中文", "a": "中文"})] == ["a", "b"]
