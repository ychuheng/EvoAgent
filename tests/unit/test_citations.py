"""逐结论引用核对的测试：拆句、级别判定与"不许把摘要当正文"。"""

from __future__ import annotations

from evoagent.evals.citations import (
    VERDICT_BACKED,
    VERDICT_SNIPPET_ONLY,
    VERDICT_UNCITED,
    VERDICT_UNKNOWN,
    check_citations,
    render_citation_report,
    report_from_trace,
    split_conclusions,
)

DOC = "https://docs.python.org/3/library/pathlib.html"
BLOG = "https://example.com/blog"
NEVER = "https://example.com/never-seen"


def levels_of(answer: str, links: list[dict[str, str]]):
    return {item.index: item.verdict for item in check_citations(answer, links).conclusions}


def test_split_keeps_bullets_and_drops_urls_from_the_text() -> None:
    answer = (
        "# 结论\n"
        f"- Python 的 pathlib 提供 Path.walk 遍历目录。{DOC}\n"
        "  - 嵌套项同样是一条需要引用的结论。\n"
        "  - 短\n"
        f"* 第二条结论，引用了另一篇资料。{BLOG}\n"
    )
    conclusions = split_conclusions(answer)
    # 嵌套项也算结论（保守处理：它会因为没有引用被标出来），过短片段丢弃。
    assert [item.text for item in conclusions] == [
        "Python 的 pathlib 提供 Path.walk 遍历目录。",
        "嵌套项同样是一条需要引用的结论。",
        "第二条结论，引用了另一篇资料。",
    ]
    assert conclusions[0].citations == (DOC,)
    assert conclusions[1].citations == ()


def test_split_handles_plain_paragraphs_by_sentence() -> None:
    answer = "第一句结论足够长可以保留。第二句结论也足够长可以保留。短句。"
    assert [item.text for item in split_conclusions(answer)] == [
        "第一句结论足够长可以保留。",
        "第二句结论也足够长可以保留。",
    ]


def test_read_text_citation_passes_snippet_only_and_uncited_fail() -> None:
    answer = (
        f"- 读过的来源支撑这条结论。{DOC}\n"
        f"- 只看到摘要的结论。{BLOG}\n"
        "- 完全没有引用的结论，不应该被算作有依据。\n"
    )
    verdicts = levels_of(
        answer, [{"url": DOC, "level": "fetched_text"}, {"url": BLOG, "level": "search_snippet"}]
    )
    assert verdicts == {1: VERDICT_BACKED, 2: VERDICT_SNIPPET_ONLY, 3: VERDICT_UNCITED}


def test_url_without_any_observation_is_unknown_source() -> None:
    verdicts = levels_of(f"- 编造来源的结论。{NEVER}\n", [])
    assert verdicts == {1: VERDICT_UNKNOWN}


def test_report_passes_only_when_every_conclusion_reads_real_text() -> None:
    good = check_citations(
        f"- 结论一。{DOC}\n- 结论二。{DOC}\n", [{"url": DOC, "level": "fetched_text"}]
    )
    assert good.passed and good.failures == ()
    assert good.counts[VERDICT_BACKED] == 2

    mixed = check_citations(
        f"- 结论一。{DOC}\n- 结论二只引摘要。{BLOG}\n",
        [{"url": DOC, "level": "fetched_text"}, {"url": BLOG, "level": "search_snippet"}],
    )
    assert not mixed.passed
    assert [item.index for item in mixed.failures] == [2]


def test_empty_answer_is_not_a_pass() -> None:
    report = check_citations("", [])
    assert report.conclusions == ()
    assert not report.passed


def test_report_from_trace_reads_answer_and_links() -> None:
    trace = {
        "final_answer": f"- 结论。{DOC}\n",
        "sources": {"answer_links": [{"url": DOC, "level": "fetched_text"}]},
    }
    assert report_from_trace(trace).passed
    # 没有 sources 时不能"默认通过"。
    assert not report_from_trace({"final_answer": "- 结论。"}).passed


def test_render_lists_failures_and_never_claims_fact_checking() -> None:
    report = check_citations("- 没引用的结论。\n", [])
    text = render_citation_report(report)
    assert "需要人工处理的结论" in text
    assert "不判断网页内容是否支持该结论" in text
