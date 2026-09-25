"""专用验收网络中的本地 ONNX Embedding 服务，返回模型原生 384 维。"""

import os
from functools import lru_cache

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field

MODEL = os.environ["EMBEDDING_MODEL"]
DIMENSION = 384
app = FastAPI()


class EmbeddingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: str
    input: list[str] = Field(min_length=1, max_length=16)
    dimensions: int = DIMENSION
    encoding_format: str = "float"


@lru_cache(maxsize=1)
def encoder():
    from fastembed import TextEmbedding

    return TextEmbedding(model_name=MODEL, cache_dir="/models")


@app.get("/health/ready")
def ready():
    return {"model": MODEL, "dimension": DIMENSION} if encoder() else None


@app.post("/v1/embeddings")
def embed(request: EmbeddingRequest):
    if request.model != MODEL or request.dimensions != DIMENSION:
        raise HTTPException(422, "model or native dimension mismatch")
    if request.encoding_format != "float" or any(not item.strip() for item in request.input):
        raise HTTPException(422, "unsupported embedding input")
    vectors = list(encoder().embed(request.input))
    if any(len(vector) != DIMENSION for vector in vectors):
        raise HTTPException(503, "encoder dimension mismatch")
    return {
        "object": "list",
        "model": MODEL,
        "data": [
            {"object": "embedding", "index": index, "embedding": vector.tolist()}
            for index, vector in enumerate(vectors)
        ],
        "usage": {"total_tokens": 0},
    }
