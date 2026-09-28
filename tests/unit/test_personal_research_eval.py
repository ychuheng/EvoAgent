from pathlib import Path

from scripts.personal_research_eval import load_dataset, score_trace


def test_research_dataset_is_frozen_and_requires_human_review() -> None:
    dataset, digest = load_dataset(Path("evals/datasets/personal-research-v1.json"))

    assert len(dataset["cases"]) == 4
    assert len(digest) == 64
    assert "事实与引用支持度" in dataset["human_scoring"]["dimensions"]


def test_matching_urls_are_only_automatic_evidence_not_fact_review() -> None:
    case = {
        "checks": {
            "min_searches": 1,
            "min_read_urls": 1,
            "min_fetched_answer_links": 1,
            "max_unobserved_answer_links": 0,
        },
        "review_questions": ["Does the page support the answer?"],
    }
    trace = {
        "final_answer": "- 结论：这个方法按目录树生成名字。https://example.org/fact\n",
        "sources": {
            "searches": [{"provider": "ddgs"}],
            "reads": [{"final_url": "https://example.org/fact"}],
            "answer_links": [{"url": "https://example.org/fact", "level": "fetched_text"}],
        },
    }

    result = score_trace(case, {"status": "completed"}, trace)

    assert result["automatic_passed"] is True
    assert result["per_conclusion"]["passed"] is True
    assert result["per_conclusion"]["counts"]["backed_by_read_text"] == 1
    assert result["human_review"]["status"] == "pending"
    trace["sources"]["answer_links"].append(
        {"url": "https://example.org/invented", "level": "unobserved"}
    )
    assert score_trace(case, {"status": "completed"}, trace)["automatic_passed"] is False


def test_conclusion_without_a_read_source_fails_the_automatic_check() -> None:
    """只看过搜索摘要、或压根没引用，都不能算"这条结论有依据"。"""

    case = {
        "checks": {
            "min_searches": 1,
            "min_read_urls": 1,
            "min_fetched_answer_links": 1,
            "max_unobserved_answer_links": 1,
        },
        "review_questions": ["Does the page support the answer?"],
    }
    trace = {
        "final_answer": (
            "- 有正文支撑的结论。https://example.org/fact\n"
            "- 只看到摘要的结论。https://example.org/snippet\n"
            "- 完全没有引用的结论，不能算有依据。\n"
        ),
        "sources": {
            "searches": [{"provider": "ddgs"}],
            "reads": [{"final_url": "https://example.org/fact"}],
            "answer_links": [
                {"url": "https://example.org/fact", "level": "fetched_text"},
                {"url": "https://example.org/snippet", "level": "search_snippet"},
            ],
        },
    }

    result = score_trace(case, {"status": "completed"}, trace)

    assert result["automatic_checks"]["conclusions_backed_by_read_text"] is False
    assert result["automatic_passed"] is False
    failures = {item["index"]: item["verdict"] for item in result["per_conclusion"]["failures"]}
    assert failures == {2: "cited_without_reading", 3: "uncited"}
