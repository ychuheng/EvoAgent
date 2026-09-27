"""Project source events into auditable search, read, and answer-link evidence."""

import re
from datetime import datetime
from typing import Any, Literal
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict

_URL_IN_ANSWER = re.compile(r"https?://[^\s<>\])]+")


class SearchEvidence(BaseModel):
    model_config = ConfigDict(frozen=True)

    tool_call_id: str
    query: str
    provider: str
    result_count: int
    urls: tuple[str, ...]
    observed_at: datetime


class ReadEvidence(BaseModel):
    model_config = ConfigDict(frozen=True)

    tool_call_id: str
    requested_url: str
    final_url: str
    content_sha256: str
    content_bytes: int
    status_code: int
    observed_at: datetime


class AnswerLinkEvidence(BaseModel):
    model_config = ConfigDict(frozen=True)

    url: str
    level: Literal["fetched_text", "search_snippet", "unobserved"]
    tool_call_id: str | None = None


class SourceReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    searches: tuple[SearchEvidence, ...] = ()
    reads: tuple[ReadEvidence, ...] = ()
    answer_links: tuple[AnswerLinkEvidence, ...] = ()


def _canonical_url(raw: str) -> str | None:
    try:
        parsed = urlsplit(raw)
        if (
            parsed.scheme.lower() not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
        ):
            return None
        return urlunsplit(
            (parsed.scheme.lower(), parsed.netloc.lower(), parsed.path or "/", parsed.query, "")
        )
    except ValueError:
        return None


def build_source_report(events: tuple[Any, ...], final_answer: str | None) -> SourceReport:
    searches: list[SearchEvidence] = []
    reads: list[ReadEvidence] = []
    observed: dict[str, tuple[str, str]] = {}
    for event in events:
        if event.event_type != "source.observed":
            continue
        payload = event.payload
        call_id = payload.get("tool_call_id")
        if not isinstance(call_id, str):
            continue
        if payload.get("level") == "search_snippet":
            query, provider, results = (
                payload.get("query"),
                payload.get("provider"),
                payload.get("results"),
            )
            if (
                not isinstance(query, str)
                or not isinstance(provider, str)
                or not isinstance(results, list)
            ):
                continue
            searches.append(
                SearchEvidence(
                    tool_call_id=call_id,
                    query=query,
                    provider=provider,
                    result_count=len(results),
                    urls=tuple(
                        result["url"]
                        for result in results
                        if isinstance(result, dict) and isinstance(result.get("url"), str)
                    ),
                    observed_at=event.created_at,
                )
            )
            for result in results:
                if isinstance(result, dict) and isinstance(result.get("url"), str):
                    canonical = _canonical_url(result["url"])
                    if canonical and canonical not in observed:
                        observed[canonical] = ("search_snippet", call_id)
        elif payload.get("level") == "fetched_text":
            requested, final, digest, size, status_code = (
                payload.get("requested_url"),
                payload.get("final_url"),
                payload.get("content_sha256"),
                payload.get("content_bytes"),
                payload.get("status_code"),
            )
            if (
                not all(isinstance(value, str) for value in (requested, final, digest))
                or not isinstance(size, int)
                or not isinstance(status_code, int)
            ):
                continue
            reads.append(
                ReadEvidence(
                    tool_call_id=call_id,
                    requested_url=requested,
                    final_url=final,
                    content_sha256=digest,
                    content_bytes=size,
                    status_code=status_code,
                    observed_at=event.created_at,
                )
            )
            for url in (requested, final):
                canonical = _canonical_url(url)
                if canonical:
                    observed[canonical] = ("fetched_text", call_id)

    links: list[AnswerLinkEvidence] = []
    seen: set[str] = set()
    for match in _URL_IN_ANSWER.finditer(final_answer or ""):
        raw = match.group().rstrip(".,;:!?，。；：！？")
        canonical = _canonical_url(raw)
        if canonical is None or canonical in seen:
            continue
        seen.add(canonical)
        level, call_id = observed.get(canonical, ("unobserved", None))
        links.append(AnswerLinkEvidence(url=raw, level=level, tool_call_id=call_id))
    return SourceReport(searches=tuple(searches), reads=tuple(reads), answer_links=tuple(links))
