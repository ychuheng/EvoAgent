"""在已授权项目内按内容搜索文本。"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

from pydantic import Field

from evoagent.core.models import ContractModel, ToolRisk
from evoagent.projects.schema import resolve_inside_root
from evoagent.tools.base import BaseTool, ToolExecutionError
from evoagent.tools.builtin.project_common import (
    IGNORE_RULES,
    relative_display,
    visible_entries,
)

DEFAULT_MAX_MATCHES = 50
HARD_MAX_MATCHES = 500
DEFAULT_MAX_FILE_BYTES = 1_000_000
HARD_MAX_FILE_BYTES = 8_000_000
MAX_LINE_CHARS = 300
MAX_DEPTH = 12


class SearchTextArguments(ContractModel):
    query: str = Field(min_length=1, max_length=512)
    path: str = Field(default=".", min_length=1, max_length=4_096)
    max_matches: int = Field(default=DEFAULT_MAX_MATCHES, ge=1, le=HARD_MAX_MATCHES)
    max_file_bytes: int = Field(default=DEFAULT_MAX_FILE_BYTES, ge=1, le=HARD_MAX_FILE_BYTES)
    case_sensitive: bool = False


class SearchTextTool(BaseTool[SearchTextArguments]):
    """按行搜索项目内文本文件，输出可回溯的相对路径与行号。"""

    name = "search_text"
    description = (
        "Search text content inside the authorized project. "
        "Returns project-relative paths with 1-based line numbers; skipped files are reported."
    )
    arguments_model = SearchTextArguments
    risk = ToolRisk.R0
    has_side_effects = False
    parallel_safe = True

    def __init__(self, root: Path) -> None:
        self._root = root

    async def invoke(self, arguments: SearchTextArguments) -> str:
        return await asyncio.to_thread(self._search, arguments)

    def _search(self, arguments: SearchTextArguments) -> str:
        _lexical, start = resolve_inside_root(self._root, arguments.path)
        if not start.exists():
            raise ToolExecutionError("搜索起点不存在")
        flags = 0 if arguments.case_sensitive else re.IGNORECASE
        try:
            pattern = re.compile(re.escape(arguments.query), flags)
        except re.error as error:  # pragma: no cover - re.escape 后不会失败
            raise ToolExecutionError("搜索表达式无效") from error

        matches: list[str] = []
        scanned = 0
        skipped_binary = 0
        skipped_large = 0
        truncated = False
        stack: list[tuple[Path, int]] = [(start, 0)]
        while stack and not truncated:
            directory, depth = stack.pop()
            if depth > MAX_DEPTH:
                continue
            for path, reason in visible_entries(directory, include_hidden=True):
                if reason is not None:
                    continue
                if path.is_dir():
                    stack.append((path, depth + 1))
                    continue
                display = relative_display(path, self._root)
                try:
                    size = path.stat().st_size
                except OSError:
                    continue
                if size > arguments.max_file_bytes:
                    skipped_large += 1
                    continue
                try:
                    content = path.read_bytes()
                    text = content.decode("utf-8")
                except (OSError, UnicodeDecodeError):
                    skipped_binary += 1
                    continue
                scanned += 1
                for number, line in enumerate(text.splitlines(), start=1):
                    if not pattern.search(line):
                        continue
                    if len(matches) >= arguments.max_matches:
                        truncated = True
                        break
                    trimmed = line.strip()
                    if len(trimmed) > MAX_LINE_CHARS:
                        trimmed = trimmed[:MAX_LINE_CHARS] + "…"
                    matches.append(f"{display}:{number}: {trimmed}")
                if truncated:
                    break

        lines = [
            f"搜索起点：{relative_display(start, self._root)}",
            f"查询：{arguments.query!r}"
            f"（{'区分' if arguments.case_sensitive else '不区分'}大小写）",
            f"命中（{len(matches)}）：",
            *[f"- {item}" for item in matches],
            f"已扫描文本文件：{scanned}；因非 UTF-8 或读取失败跳过：{skipped_binary}；"
            f"因超过 {arguments.max_file_bytes} 字节跳过：{skipped_large}",
        ]
        if truncated:
            lines.append(f"已达命中上限 {arguments.max_matches}；请缩小范围或提高 max_matches。")
        if not matches:
            lines.append("没有命中；可以换关键词，或先用 find_files 确认文件是否存在。")
        lines.append("忽略规则：")
        for rule in IGNORE_RULES:
            lines.append(f"- {rule.reason}: {rule.detail}")
        return "\n".join(lines)
