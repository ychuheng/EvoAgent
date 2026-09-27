"""按 glob 模式在已授权项目内查找文件。"""

from __future__ import annotations

import asyncio
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

DEFAULT_MAX_RESULTS = 100
HARD_MAX_RESULTS = 1_000
MAX_DEPTH = 12


class FindFilesArguments(ContractModel):
    pattern: str = Field(min_length=1, max_length=256)
    path: str = Field(default=".", min_length=1, max_length=4_096)
    max_results: int = Field(default=DEFAULT_MAX_RESULTS, ge=1, le=HARD_MAX_RESULTS)


class FindFilesTool(BaseTool[FindFilesArguments]):
    """在项目内深度优先匹配文件名或相对路径。"""

    name = "find_files"
    description = (
        "Find files by glob pattern inside the authorized project. "
        "The pattern matches both the file name and the project-relative path."
    )
    arguments_model = FindFilesArguments
    risk = ToolRisk.R0
    has_side_effects = False
    parallel_safe = True

    def __init__(self, root: Path) -> None:
        self._root = root

    async def invoke(self, arguments: FindFilesArguments) -> str:
        return await asyncio.to_thread(self._find, arguments)

    def _find(self, arguments: FindFilesArguments) -> str:
        _lexical, start = resolve_inside_root(self._root, arguments.path)
        if not start.exists():
            raise ToolExecutionError("搜索起点不存在")
        if not start.is_dir():
            raise ToolExecutionError("搜索起点不是一个目录")

        matches: list[str] = []
        scanned = 0
        skipped_directories = 0
        truncated = False
        stack: list[tuple[Path, int]] = [(start, 0)]
        while stack:
            directory, depth = stack.pop()
            if depth > MAX_DEPTH:
                continue
            for path, reason in visible_entries(directory, include_hidden=True):
                if reason is not None:
                    # visible_entries 已把忽略目录挡在外面（不会 yield），这里只会遇到被忽略的文件。
                    continue
                if path.is_dir():
                    stack.append((path, depth + 1))
                    continue
                scanned += 1
                display = relative_display(path, self._root)
                if _matches(arguments.pattern, path.name, display):
                    if len(matches) >= arguments.max_results:
                        truncated = True
                        break
                    matches.append(display)
            if truncated:
                break

        lines = [
            f"搜索起点：{relative_display(start, self._root)}",
            f"模式：{arguments.pattern}",
            f"命中（{len(matches)}）：",
            *[f"- {item}" for item in matches],
            f"已扫描文件：{scanned}；按忽略规则跳过的目录：{skipped_directories}",
        ]
        if truncated:
            lines.append(f"已达结果上限 {arguments.max_results}；请缩小范围或提高 max_results。")
        if not matches:
            lines.append("没有命中；可以换更宽的模式，或先用 list_dir 确认目录结构。")
        lines.append("忽略规则：")
        for rule in IGNORE_RULES:
            lines.append(f"- {rule.reason}: {rule.detail}")
        return "\n".join(lines)


def _matches(pattern: str, name: str, display: str) -> bool:
    candidates = (name, display, Path(display).name)
    return any(Path(candidate).match(pattern) for candidate in candidates)
