"""M5 调研任务的**逐结论引用核对**（离线、确定性）。

计划要求"多来源阅读 + 逐结论引用 + 人工事实评审"。已有能力与缺口：

- `trace.sources.build_source_report` 会把答复里出现的每个 URL 标成
  `fetched_text`（读过正文）/ `search_snippet`（只看到摘要）/ `unobserved`（没有任何记录）；
- 但它**不区分结论**：一段答复里可能既有被正文支撑的结论，也有只引了搜索摘要、
  甚至完全没引用的结论，而"整段有引用"会被误当成"每条结论都有依据"。

这个模块补上"逐结论"这一层：把答复拆成结论，逐条列出它引了哪些来源、这些来源处于哪一级，
并给出机械判定（**引用是否真的读过正文**）。它**不做事实核查**——来源是否支持该结论仍需人看，
所以报告末尾固定附上待人工核对的问题。
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

URL_PATTERN = re.compile(r"https?://[^\s<>\])]+")
# 列表项与句子边界：结论通常以项目符号或句末标点分隔。
_BULLET = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")
_SENTENCE_END = re.compile(r"(?<=[。！？；!?;])|(?<=\.)\s+")

VERDICT_BACKED = "backed_by_read_text"
VERDICT_SNIPPET_ONLY = "cited_without_reading"
VERDICT_UNCITED = "uncited"
VERDICT_UNKNOWN = "unknown_source"

_VERDICT_TEXT = {
    VERDICT_BACKED: "引用的来源**读过正文**（机械通过，内容是否支持仍需人工判定）",
    VERDICT_SNIPPET_ONLY: "只引用了搜索摘要，没有读过正文",
    VERDICT_UNCITED: "**没有任何引用**",
    VERDICT_UNKNOWN: "引用的 URL 在本次运行的检索/读取记录里**找不到**",
}

MIN_CONCLUSION_CHARS = 6
# 去掉 URL 之后残留的分隔符；**保留**句末标点，它是"这是一句结论"的信号。
_TRAILING_SEPARATORS = " \t-—:：,，、"


@dataclass(frozen=True, slots=True)
class Conclusion:
    index: int
    text: str
    citations: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ConclusionVerdict:
    index: int
    text: str
    citations: tuple[str, ...]
    levels: tuple[str, ...]
    verdict: str
    detail: str

    @property
    def passed(self) -> bool:
        return self.verdict == VERDICT_BACKED


@dataclass(frozen=True, slots=True)
class CitationReport:
    conclusions: tuple[ConclusionVerdict, ...]
    counts: dict[str, int]
    note: str

    @property
    def passed(self) -> bool:
        """机械门槛：每条结论都至少引用了一个**读过正文**的来源。"""

        return bool(self.conclusions) and all(item.passed for item in self.conclusions)

    @property
    def failures(self) -> tuple[ConclusionVerdict, ...]:
        return tuple(item for item in self.conclusions if not item.passed)


def _strip_urls(text: str) -> str:
    return URL_PATTERN.sub("", text).strip().strip(_TRAILING_SEPARATORS)


def split_conclusions(answer: str) -> tuple[Conclusion, ...]:
    """把答复拆成结论：列表项优先，其次按句末标点切句，丢弃过短片段。"""

    conclusions: list[Conclusion] = []
    blocks: list[str] = []
    for raw_line in answer.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if _BULLET.match(line):
            blocks.append(_BULLET.sub("", line))
            continue
        if line.startswith("#"):
            continue
        for piece in _SENTENCE_END.split(line):
            if piece and piece.strip():
                blocks.append(piece.strip())
    for block in blocks:
        citations = tuple(dict.fromkeys(URL_PATTERN.findall(block)))
        text = _strip_urls(block)
        if len(text) < MIN_CONCLUSION_CHARS and not citations:
            continue
        conclusions.append(Conclusion(index=len(conclusions) + 1, text=text, citations=citations))
    return tuple(conclusions)


def _level_of(link: Mapping[str, object] | object) -> tuple[str, str]:
    if isinstance(link, Mapping):
        return str(link.get("url", "")), str(link.get("level", "unobserved"))
    return str(getattr(link, "url", "")), str(getattr(link, "level", "unobserved"))


def check_citations(
    answer: str,
    links: Iterable[Mapping[str, object] | object],
) -> CitationReport:
    """逐结论核对引用级别；`links` 取 `RunTrace.sources.answer_links`。"""

    levels: dict[str, str] = {}
    for link in links:
        url, level = _level_of(link)
        if url:
            levels[url] = level

    verdicts: list[ConclusionVerdict] = []
    for conclusion in split_conclusions(answer):
        cited_levels = tuple(levels.get(url, "unobserved") for url in conclusion.citations)
        if not conclusion.citations:
            verdict, detail = VERDICT_UNCITED, _VERDICT_TEXT[VERDICT_UNCITED]
        elif all(level == "unobserved" for level in cited_levels):
            verdict, detail = VERDICT_UNKNOWN, _VERDICT_TEXT[VERDICT_UNKNOWN]
        elif all(level != "fetched_text" for level in cited_levels):
            verdict, detail = VERDICT_SNIPPET_ONLY, _VERDICT_TEXT[VERDICT_SNIPPET_ONLY]
        else:
            verdict, detail = VERDICT_BACKED, _VERDICT_TEXT[VERDICT_BACKED]
        verdicts.append(
            ConclusionVerdict(
                index=conclusion.index,
                text=conclusion.text,
                citations=conclusion.citations,
                levels=cited_levels,
                verdict=verdict,
                detail=detail,
            )
        )

    counts = {
        VERDICT_BACKED: sum(1 for item in verdicts if item.verdict == VERDICT_BACKED),
        VERDICT_SNIPPET_ONLY: sum(1 for item in verdicts if item.verdict == VERDICT_SNIPPET_ONLY),
        VERDICT_UNCITED: sum(1 for item in verdicts if item.verdict == VERDICT_UNCITED),
        VERDICT_UNKNOWN: sum(1 for item in verdicts if item.verdict == VERDICT_UNKNOWN),
    }
    note = (
        "本报告只核对「引用是否真的读过正文」，**不判断网页内容是否支持该结论**；"
        "通过之后仍需人工按引用逐条核对事实，并把结论、反例与不确定项写进人评表。"
    )
    return CitationReport(conclusions=tuple(verdicts), counts=counts, note=note)


def report_from_trace(trace: Mapping[str, object]) -> CitationReport:
    """从 `RunTrace`（API/JSON）里取答复与 `sources.answer_links` 直接核对。"""

    answer = str(trace.get("final_answer") or "")
    sources = trace.get("sources") or {}
    links = sources.get("answer_links") if isinstance(sources, Mapping) else None
    return check_citations(answer, links or ())


def render_citation_report(report: CitationReport) -> str:
    """渲染成给人读的核对表（Markdown）。"""

    lines = [
        "# 逐结论引用核对",
        "",
        f"- 结论条数：{len(report.conclusions)}",
        f"- 读过正文：{report.counts[VERDICT_BACKED]}；只引摘要："
        f"{report.counts[VERDICT_SNIPPET_ONLY]}；无引用：{report.counts[VERDICT_UNCITED]}；"
        f"来源不明：{report.counts[VERDICT_UNKNOWN]}",
        f"- 机械结论：{'通过' if report.passed else '**未通过**'}",
        "",
        f"> {report.note}",
        "",
        "| # | 结论 | 引用级别 | 判定 |",
        "| --- | --- | --- | --- |",
    ]
    for item in report.conclusions:
        levels = "、".join(item.levels) or "—"
        lines.append(f"| {item.index} | {item.text} | {levels} | {item.detail} |")
    if report.failures:
        lines += ["", "## 需要人工处理的结论", ""]
        lines += [f"- 第 {item.index} 条：{item.detail}——{item.text}" for item in report.failures]
    return "\n".join(lines) + "\n"
