import pytest
from pydantic import ValidationError

from evoagent.evals.runtime_schema import (
    RuntimeArm,
    RuntimeExperimentSpec,
    retrieval_metrics,
    usage_breakdown,
)


def spec(**kwargs):
    return RuntimeExperimentSpec(
        experiment_variable="context_policy",
        control=RuntimeArm(),
        model="mock-model",
        code_version="test",
        dataset_hash="sha256:" + "a" * 64,
        environment={"cpu": "2", "memory": "1GB"},
        **kwargs,
    )


def test_single_declared_variable_required():
    assert spec(treatment=RuntimeArm(context_policy="legacy"))
    with pytest.raises(ValidationError):
        spec(treatment=RuntimeArm())
    with pytest.raises(ValidationError):
        spec(treatment=RuntimeArm(context_policy="legacy", memory_mode="read_only"))


def test_unknown_auxiliary_usage_never_becomes_zero():
    report = usage_breakdown(
        {"main": 50, "summarizer": None, "memory_generator": 0, "embedding": 12}
    )
    assert report["exact_model_tokens"] is None
    assert report["known_model_tokens"] == 50
    assert report["embedding_tokens"] == 12


def test_retrieval_metrics_include_empty_relevance_and_deduplicate():
    assert retrieval_metrics(["a", "a", "b"], {"b", "c"}, 3) == {
        "recall_at_k": 0.5,
        "mrr": 0.5,
        "irrelevant_forced_hit": False,
    }
    assert retrieval_metrics(["a"], [], 1)["irrelevant_forced_hit"] is True


def test_usage_comes_from_completion_events_even_without_tool_turns():
    from types import SimpleNamespace

    from evoagent.evals.metrics import model_usage_rows

    usage = {"input_tokens": 9, "output_tokens": 1, "total_tokens": 10}
    completed = SimpleNamespace(event_type="model.completed", payload={"usage": usage})
    assert model_usage_rows([], [completed]) == [usage]
    assert model_usage_rows([SimpleNamespace(usage=usage)], [completed]) == [usage]
    failed = SimpleNamespace(event_type="model.failed", payload={"error_code": "provider_timeout"})
    assert model_usage_rows([], [completed, failed]) == [usage, None]
    quota = SimpleNamespace(event_type="model.failed", payload={"error_code": "rate_limited"})
    assert model_usage_rows([], [completed, quota]) == [usage]
