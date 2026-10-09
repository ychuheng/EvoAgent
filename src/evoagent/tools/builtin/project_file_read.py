"""按需读取已授权项目内的文本文件：编码识别、二进制拒绝、分段与行范围。

对应实施计划 §6 的 P-04：

- 路径经 `resolve_inside_root` 规范化，`..`、绝对路径越界、符号链接/junction 逃逸全部拒绝。
- 文件过大时不整文件塞进上下文：给出**继续读取的方式**（起始行）。
- 非 UTF-8 或含 NUL 的文件按二进制拒绝，并说明原因。
"""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

from pydantic import Field

from evoagent.core.models import ContractModel, ToolRisk
from evoagent.projects.schema import resolve_inside_root
from evoagent.tools.base import BaseTool, ToolExecutionError
from evoagent.tools.builtin.project_common import ensure_not_symlink, relative_display

# 单次返回给模型的最大字符数：超出的部分分片返回。
DEFAULT_MAX_CHARS = 40_000
HARD_MAX_CHARS = 200_000
DEFAULT_MAX_LINES = 400
HARD_MAX_LINES = 5_000
# 读取前先看头部的字节数，用于二进制探测。
SNIFF_BYTES = 8_192
DEFAULT_MAX_FILE_BYTES = 8 * 1024 * 1024


class ProjectFileReadArguments(ContractModel):
    path: str = Field(min_length=1, max_length=4_096)
    start_line: int = Field(default=1, ge=1, le=10_000_000)
    max_lines: int = Field(default=DEFAULT_MAX_LINES, ge=1, le=HARD_MAX_LINES)
    max_chars: int = Field(default=DEFAULT_MAX_CHARS, ge=1, le=HARD_MAX_CHARS)


class ProjectFileReadTool(BaseTool[ProjectFileReadArguments]):
    """读取项目内一个文本文件的一段行范围。"""

    name = "file_read"
    description = (
        "Read a line range from a UTF-8 text file inside the authorized project. "
        "Binary files are refused; oversized files return how to continue reading."
    )
    arguments_model = ProjectFileReadArguments
    risk = ToolRisk.R0
    has_side_effects = False
    parallel_safe = True

    def __init__(self, root: Path, *, max_file_bytes: int = DEFAULT_MAX_FILE_BYTES) -> None:
        if not 1 <= max_file_bytes <= DEFAULT_MAX_FILE_BYTES:
            raise ValueError("max_file_bytes must be within 1..8 MiB")
        self._root = root
        self._max_file_bytes = max_file_bytes

    async def invoke(self, arguments: ProjectFileReadArguments) -> str:
        return await asyncio.to_thread(self._read, arguments)

    def _read(self, arguments: ProjectFileReadArguments) -> str:
        _lexical, physical = resolve_inside_root(self._root, arguments.path)
        if not physical.exists():
            raise ToolExecutionError("文件不存在")
        ensure_not_symlink(physical)
        if not physical.is_file():
            raise ToolExecutionError("路径不是一个普通文件")

        display = relative_display(physical, self._root)
        try:
            with physical.open("rb") as handle:
                # One bounded descriptor read also covers a file growing after
                # stat. A line range is an output bound, not an input byte bound.
                raw = handle.read(self._max_file_bytes + 1)
            if len(raw) > self._max_file_bytes:
                raise ToolExecutionError(
                    f"文件超过读取上限 {self._max_file_bytes} 字节；请用 search_text 有界定位"
                )
            head = raw[:SNIFF_BYTES]
            if b"\x00" in head:
                raise ToolExecutionError(
                    f"{display} 看起来是二进制文件（含 NUL 字节）；请改用 search_text 定位文本片段"
                )
        except OSError as error:
            raise ToolExecutionError(f"文件无法读取：{display}") from error

        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as error:
            raise ToolExecutionError(
                f"{display} 不是 UTF-8 文本（在偏移 {error.start} 处失败）；"
                "请先用 search_text 确认内容，或改用其它文件"
            ) from error

        newline = "\r\n" if "\r\n" in text else "\n"
        lines = text.splitlines()
        total_lines = len(lines)
        if total_lines == 0:
            return (
                f"文件：{display}\n原文件 SHA-256：{hashlib.sha256(raw).hexdigest()}"
                "\n（空文件，0 行）"
            )

        start = arguments.start_line
        if start > total_lines:
            raise ToolExecutionError(
                f"起始行 {start} 超过文件总行数 {total_lines}；"
                f"请用 start_line <= {total_lines} 重新读取"
            )
        selected = lines[start - 1 : start - 1 + arguments.max_lines]
        numbered = [f"{start + index}: {line}" for index, line in enumerate(selected)]
        body = newline.join(numbered)
        truncated_by_chars = len(body) > arguments.max_chars
        if truncated_by_chars:
            body = body[: arguments.max_chars]
            body += f"{newline}…（本片按 {arguments.max_chars} 字符截断）"

        last_line = start + len(selected) - 1
        header = [
            f"文件：{display}",
            f"原文件 SHA-256：{hashlib.sha256(raw).hexdigest()}",
            f"总行数：{total_lines}；本次返回第 {start}–{last_line} 行"
            f"（上限 {arguments.max_lines} 行 / {arguments.max_chars} 字符）",
        ]
        footer: list[str] = []
        if truncated_by_chars:
            footer.append(f"内容因字符上限被截断；可用 start_line={last_line + 1} 继续读取。")
        if last_line < total_lines:
            footer.append(
                f"还有 {total_lines - last_line} 行未读；用 start_line={last_line + 1} 继续。"
            )
        else:
            footer.append("已读到文件末尾。")
        parts = [*header, "", body]
        if footer:
            parts.extend(["", *footer])
        return "\n".join(parts)
