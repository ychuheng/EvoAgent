from evoagent.tools.builtin.web_fetch import _model_visible_text


def test_query_reads_late_section_from_long_html_page() -> None:
    html = (
        "<main>"
        + "<p>introduction</p>" * 1_200
        + "<h2>Path.walk</h2><p>Generate names in a directory tree.</p>"
        + "</main>"
    )
    first, first_truncated = _model_visible_text(html, "text/html")
    excerpt, excerpt_truncated = _model_visible_text(html, "text/html", query="Path.walk")
    assert first_truncated and "Path.walk" not in first
    assert excerpt_truncated
    assert "Path.walk" in excerpt
    assert "Generate names in a directory tree" in excerpt


def test_missing_query_does_not_claim_page_was_read() -> None:
    excerpt, _ = _model_visible_text(
        "<main><p>Another method</p></main>", "text/html", query="Path.walk"
    )
    assert "未找到查询片段" in excerpt
    assert "Path.walk" in excerpt


def test_query_prefers_definition_before_footer_reference() -> None:
    html = (
        "<main>"
        + "<p>introduction</p>" * 1_200
        + "<h2>Path.walk</h2><p>Generate names in a directory tree.</p>"
        + "<p>other methods</p>" * 1_200
        + "<p>For comparison, see Path.walk.</p>"
        + "</main>"
    )
    excerpt, _ = _model_visible_text(html, "text/html", query="Path.walk")
    assert "Generate names in a directory tree" in excerpt
