"""单变量实验契约；配置、语料与环境均进入内容哈希。"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class RuntimeArm(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    context_policy: Literal["bounded", "legacy"] = "bounded"
    memory_mode: Literal["off", "read_only"] = "off"
    retrieval_backend: Literal["lexical", "hybrid"] = "lexical"
    worker_count: int = Field(default=1, ge=1, le=16)


class RuntimeExperimentSpec(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    schema_version: Literal[1] = 1
    experiment_variable: Literal[
        "context_policy", "memory_mode", "retrieval_backend", "worker_count"
    ]
    control: RuntimeArm
    treatment: RuntimeArm
    repeats: int = Field(default=3, ge=1, le=100)
    provider: Literal["mock", "openai_compatible"] = "mock"
    provider_thinking_mode: Literal["disabled"] | None = None
    model: str = Field(min_length=1, max_length=256)
    code_version: str = Field(min_length=1, max_length=128)
    dataset_hash: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
    environment: dict[str, str]
    context_window_tokens: int = Field(default=32768, ge=1024)
    max_output_tokens: int = Field(default=4096, ge=1)
    context_safety_margin: int = Field(default=1024, ge=0)
    max_total_tokens: int = Field(default=32000, ge=1)
    max_iterations: int = Field(default=8, ge=1, le=100)
    embedding_model: str = "mock-hash-v1"
    memory_retrieval_top_k: int = Field(default=3, ge=1, le=10)
    retrieval_min_lexical_score: float = Field(default=0.1, gt=0)
    retrieval_max_vector_distance: float = Field(default=0.35, ge=0, lt=1)
    retrieval_rrf_k: int = Field(default=60, ge=1)
    retrieval_memory_budget: int = Field(default=2000, ge=1, le=32000)

    @model_validator(mode="after")
    def single_variable(self):
        left, right = self.control.model_dump(), self.treatment.model_dump()
        changed = {key for key in left if left[key] != right[key]}
        if changed != {self.experiment_variable}:
            raise ValueError("exactly the declared experiment variable must differ")
        if not self.environment:
            raise ValueError("record resource quotas and execution environment")
        if self.context_window_tokens <= self.max_output_tokens + self.context_safety_margin:
            raise ValueError("context window has no input budget")
        return self


def usage_breakdown(components, known_subtotals=None):
    """组件必须明确记为 0（未调用）或 None（调用但服务未返回）。"""
    required = ("main", "summarizer", "memory_generator")
    known_subtotals = known_subtotals or {}
    known = sum(
        components.get(key) if components.get(key) is not None else known_subtotals.get(key, 0)
        for key in required
    )
    complete = all(components.get(key) is not None for key in required)
    return {
        "components": components,
        "known_model_tokens": known,
        "exact_model_tokens": known if complete else None,
        "embedding_tokens": components.get("embedding"),
    }


def retrieval_metrics(ranked, relevant, k):
    relevant = set(relevant)
    selected = list(dict.fromkeys(ranked))[:k]
    hits = relevant.intersection(selected)
    return {
        "recall_at_k": len(hits) / len(relevant) if relevant else None,
        "mrr": next((1 / (i + 1) for i, key in enumerate(selected) if key in relevant), 0),
        "irrelevant_forced_hit": bool(selected) and not relevant,
    }


def runtime_pair_comparable(left, right, spec):
    """只归一化已声明变量及隔离 fixture 的派生身份；不修改 Skill 可比规则。"""
    from copy import deepcopy

    if left is None or right is None:
        return False
    configs = []
    for config, arm in ((left, spec.control), (right, spec.treatment)):
        value = deepcopy(config)
        if value.get("provider") != spec.provider or value.get("model") != spec.model:
            return False
        if value.get("skill_retrieval_top_k") != 0 or value.get("selected_skills"):
            return False
        retrieval = value.get("retrieval")
        expected_retrieval = arm.memory_mode == "read_only" or arm.retrieval_backend == "hybrid"
        if (retrieval is not None) != expected_retrieval:
            return False
        if retrieval is not None:
            expected = {
                "backend": arm.retrieval_backend,
                "memory": arm.memory_mode == "read_only",
                "archive": False,
                "model": spec.embedding_model,
                "memory_top_k": spec.memory_retrieval_top_k,
                "lexical_threshold": spec.retrieval_min_lexical_score,
                "distance_threshold": spec.retrieval_max_vector_distance,
                "rrf_k": spec.retrieval_rrf_k,
                "memory_budget": spec.retrieval_memory_budget,
            }
            if any(
                retrieval.get(key) != expected_value for key, expected_value in expected.items()
            ):
                return False
            # 不同隔离工作区的版本 ID 不相等；实际来源 hash 保留在 evidence 中。
            for key in ("selection_hash", "profile_id", "generation", "degraded"):
                retrieval.pop(key, None)
            if spec.experiment_variable == "retrieval_backend":
                retrieval["backend"] = "declared-variable"
        if spec.experiment_variable == "memory_mode":
            # off 臂没有 RetrievalBatch；开启臂的所有公共参数已逐项对照 Spec。
            value["retrieval"] = None
        if spec.experiment_variable == "context_policy":
            value.pop("context_policy", None)
            value.pop("max_output_tokens", None)
        configs.append(value)
    return configs[0] == configs[1]
