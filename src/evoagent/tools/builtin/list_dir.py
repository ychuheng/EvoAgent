"""列出已授权项目内的目录条目。"""

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

DEFAULT_MAX_ENTRIES = 200
HARD_MAX_ENTRIES = 1_000


class ListDirArguments(ContractModel):
    path: str = Field(default=".", min_length=1, max_length=4_096)
    include_hidden: bool = False
    max_entries: int = Field(default=DEFAULT_MAX_ENTRIES, ge=1, le=HARD_MAX_ENTRIES)


class ListDirTool(BaseTool[ListDirArguments]):
    """列出项目内一个目录的直接子项，附带可说明的忽略原因。"""

    name = "list_dir"
    description = (
        "List the direct children of a directory inside the authorized project. "
        "Build artifacts, caches and VCS metadata are skipped and reported with a reason."
    )
    arguments_model = ListDirArguments
    risk = ToolRisk.R0
    has_side_effects = False
    parallel_safe = True

    def __init__(self, root: Path) -> None:
        self._root = root

    async def invoke(self, arguments: ListDirArguments) -> str:
        return await asyncio.to_thread(self._list, arguments)

    def _list(self, arguments: ListDirArguments) -> str:
        _lexical, physical = resolve_inside_root(self._root, arguments.path)
        if not physical.exists():
            raise ToolExecutionError("目录不存在")
        if not physical.is_dir():
            raise ToolExecutionError("路径不是一个目录")

        entries: list[str] = []
        ignored: dict[str, list[str]] = {}
        truncated = False
        for path, reason in visible_entries(physical, include_hidden=arguments.include_hidden):
            display = relative_display(path, self._root)
            if reason is not None:
                ignored.setdefault(reason, []).append(display)
                continue
            if len(entries) >= arguments.max_entries:
                truncated = True
                break
            try:
                is_directory = path.is_dir()
            except OSError:  # pragma: no cover - 竞态删除
                continue
            suffix = "/" if is_directory else ""
            entries.append(f"{display}{suffix}")

        lines = [
            f"目录：{relative_display(physical, self._root)}",
            f"条目（{len(entries)}）：",
            *[f"- {item}" for item in entries],
        ]
        if truncated:
            lines.append(f"已达条目上限 {arguments.max_entries}；请缩小目录或提高 max_entries。")
        if ignored:
            lines.append("被忽略（按原因）：")
            for reason, items in sorted(ignored.items()):
                shown = items[:10]
                more = "" if len(items) <= len(shown) else f" 等 {len(items)} 项"
                lines.append(f"- {reason}: {', '.join(shown)}{more}")
        lines.append("忽略规则：")
        for rule in IGNORE_RULES:
            lines.append(f"- {rule.reason}: {rule.detail}")
        return "\n".join(lines)
