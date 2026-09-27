from datetime import UTC, datetime

from evoagent.trace.service import TraceEvent
from evoagent.trace.sources import build_source_report


def event(sequence: int, payload: dict) -> TraceEvent:
    return TraceEvent(
        sequence=sequence,
        event_type="source.observed",
        payload=payload,
        schema_version=1,
        created_at=datetime(2026, 9, 27, tzinfo=UTC),
    )


def test_source_report_distinguishes_read_snippet_and_unobserved_answer_links() -> None:
    events = (
        event(
            1,
            {
                "tool_call_id": "search-1",
                "level": "search_snippet",
                "query": "three projects",
                "provider": "mock",
                "results": [
                    {"url": "https://example.com/a", "title": "A", "snippet": "summary"},
                    {"url": "https://example.com/b", "title": "B", "snippet": "summary"},
                ],
            },
        ),
        event(
            2,
            {
                "tool_call_id": "fetch-1",
                "level": "fetched_text",
                "requested_url": "https://example.com/a",
                "final_url": "https://example.com/a-final",
                "content_sha256": "a" * 64,
                "content_bytes": 42,
                "status_code": 200,
            },
        ),
    )
    answer = (
        "[A](https://example.com/a) [B](https://example.com/b) "
        "[C](https://example.com/c), [final](https://example.com/a-final#top)"
    )

    report = build_source_report(events, answer)

    assert report.searches[0].query == "three projects"
    assert report.searches[0].urls == ("https://example.com/a", "https://example.com/b")
    assert report.reads[0].content_sha256 == "a" * 64
    assert [(link.level, link.tool_call_id) for link in report.answer_links] == [
        ("fetched_text", "fetch-1"),
        ("search_snippet", "search-1"),
        ("unobserved", None),
        ("fetched_text", "fetch-1"),
    ]


def test_zero_results_remain_a_search_record_without_invented_source() -> None:
    report = build_source_report(
        (
            event(
                1,
                {
                    "tool_call_id": "search-1",
                    "level": "search_snippet",
                    "query": "nothing",
                    "provider": "brave",
                    "results": [],
                },
            ),
        ),
        "No public result was found.",
    )

    assert report.searches[0].result_count == 0
    assert report.searches[0].urls == ()
    assert report.reads == ()
    assert report.answer_links == ()
