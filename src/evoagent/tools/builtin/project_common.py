"""项目只读发现的公共部分：忽略规则、条目模型与遍历辅助。

对应实施计划 §6 的 P-03/P-04：分页/条数/字节上限、忽略常见构建产物和 `.git`，
并且**忽略规则必须能说明**——每个被跳过的条目都带原因，输出里也会回显规则本身。
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from evoagent.tools.base import ToolExecutionError

# 目录级忽略：构建产物、缓存、虚拟环境、版本库元数据。
IGNORED_DIRECTORY_NAMES = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        ".venv",
        "__pycache__",
        "build",
        "dist",
        "node_modules",
        "venv",
    }
)
# 文件级忽略：字节码与二进制产物。
IGNORED_FILE_SUFFIXES = (".pyc", ".pyo", ".pyd")

HIDDEN_PREFIX = "."


@dataclass(frozen=True, slots=True)
class IgnoreRule:
    """一条可说明的忽略规则。"""

    reason: str
    detail: str

    def as_dict(self) -> dict[str, str]:
        return {"reason": self.reason, "detail": self.detail}


IGNORE_RULES: tuple[IgnoreRule, ...] = (
    IgnoreRule(
        reason="vcs_metadata",
        detail="跳过版本库元数据目录（.git/.hg/.svn）",
    ),
    IgnoreRule(
        reason="build_or_cache_directory",
        detail="跳过构建产物与缓存目录："
        + ", ".join(sorted(IGNORED_DIRECTORY_NAMES - {".git", ".hg", ".svn"})),
    ),
    IgnoreRule(
        reason="compiled_artifact",
        detail="跳过编译产物：" + ", ".join(IGNORED_FILE_SUFFIXES),
    ),
)


def ignore_reason(name: str, *, is_directory: bool) -> str | None:
    """返回该条目被忽略的原因；不忽略则返回 None。"""

    lowered = name.lower()
    if is_directory and lowered in IGNORED_DIRECTORY_NAMES:
        return "vcs_metadata" if lowered in {".git", ".hg", ".svn"} else "build_or_cache_directory"
    if not is_directory and lowered.endswith(IGNORED_FILE_SUFFIXES):
        return "compiled_artifact"
    return None


def visible_entries(directory: Path, *, include_hidden: bool) -> Iterator[tuple[Path, str | None]]:
    """按名称顺序产出 (路径, 忽略原因)。"""

    try:
        names = sorted(os.scandir(directory), key=lambda item: item.name)
    except OSError as error:
        raise ToolExecutionError(f"目录无法列举：{directory.name}") from error
    for entry in names:
        try:
            is_directory = entry.is_dir(follow_symlinks=False)
        except OSError:  # pragma: no cover - 竞态删除
            continue
        reason = ignore_reason(entry.name, is_directory=is_directory)
        if reason is None and not include_hidden and entry.name.startswith(HIDDEN_PREFIX):
            reason = "hidden_entry"
        yield Path(entry.path), reason


def relative_display(path: Path, root: Path) -> str:
    """把绝对路径转成相对根的正斜杠形式，保证输出可回溯且不泄露宿主前缀。"""

    try:
        return path.relative_to(root).as_posix()
    except ValueError:  # pragma: no cover - 调用方已保证在根内
        return path.name


def ensure_not_symlink(path: Path) -> None:
    """拒绝把符号链接本身当作普通项目文件处理。"""

    if path.is_symlink():
        raise ToolExecutionError("拒绝读取符号链接本身；请使用它指向的真实项目内路径")
