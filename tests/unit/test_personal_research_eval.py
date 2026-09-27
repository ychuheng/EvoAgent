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
        "sources": {
            "searches": [{"provider": "ddgs"}],
            "reads": [{"final_url": "https://example.org/fact"}],
            "answer_links": [{"url": "https://example.org/fact", "level": "fetched_text"}],
        }
    }

    result = score_trace(case, {"status": "completed"}, trace)

    assert result["automatic_passed"] is True
    assert result["human_review"]["status"] == "pending"
    trace["sources"]["answer_links"].append(
        {"url": "https://example.org/invented", "level": "unobserved"}
    )
    assert score_trace(case, {"status": "completed"}, trace)["automatic_passed"] is False
