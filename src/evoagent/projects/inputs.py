"""冻结的 Task 输入集（实施计划 §10 F-02）。

用户显式提供一组文件（落在已授权项目根内）→ 在 Task 创建时**冻结内容哈希** →
运行中若文件被替换，任务以 `input_changed` 终止，而不是悄悄换掉输入。

这与"后台扫描 Downloads"相反：输入集只由用户提交的路径组成，且每条都要能解析到授权根内。
"""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator

from evoagent.projects.schema import resolve_inside_root
from evoagent.tools.base import ToolExecutionError, ToolPermissionError

MAX_INPUT_FILES = 64
MAX_INPUT_BYTES = 64 * 1024 * 1024

# 只做"类型标签"用，不做内容解析（解析属 F-03）。
_TEXT_SUFFIXES = frozenset(
    {
        ".md",
        ".txt",
        ".json",
        ".csv",
        ".py",
        ".log",
        ".yaml",
        ".yml",
        ".html",
        ".xml",
        ".toml",
        ".ini",
        ".cfg",
    }
)
_DOCUMENT_SUFFIXES = frozenset({".pdf", ".docx", ".doc", ".odt"})
_IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"})


class InputChangedError(ToolExecutionError):
    """冻结的输入集在运行中被改动。"""

    code = "input_changed"


class FrozenInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str = Field(min_length=1, max_length=4_096)
    sha256: str = Field(min_length=64, max_length=64)
    size_bytes: int = Field(ge=0)
    kind: str = Field(min_length=1, max_length=32)


class InputSet(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    files: tuple[FrozenInput, ...] = Field(min_length=1, max_length=MAX_INPUT_FILES)

    @model_validator(mode="after")
    def unique_paths(self) -> InputSet:
        paths = [item.path for item in self.files]
        if len(set(paths)) != len(paths):
            raise ValueError("输入文件路径不能重复")
        return self


@dataclass(frozen=True, slots=True)
class InputFile:
    path: str
    sha256: str
    size_bytes: int
    kind: str

    def as_model(self) -> FrozenInput:
        return FrozenInput(
            path=self.path, sha256=self.sha256, size_bytes=self.size_bytes, kind=self.kind
        )


def classify(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in _TEXT_SUFFIXES:
        return "text"
    if suffix in _DOCUMENT_SUFFIXES:
        return "document"
    if suffix in _IMAGE_SUFFIXES:
        return "image"
    return "binary"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def describe_file(root: Path, requested: str) -> InputFile:
    """把一个请求路径解析成可冻结的输入描述；越界或不是普通文件直接拒绝。"""

    _lexical, physical = resolve_inside_root(root, requested)
    if physical.is_symlink():
        raise ToolPermissionError(f"{requested} 是符号链接；请提供它指向的真实文件")
    if not physical.exists():
        raise ToolExecutionError(f"输入文件不存在：{requested}")
    if not physical.is_file():
        raise ToolExecutionError(f"输入不是普通文件：{requested}")
    try:
        size = physical.stat().st_size
    except OSError as error:
        raise ToolExecutionError(f"输入文件无法读取：{requested}") from error
    if size > MAX_INPUT_BYTES:
        raise ToolExecutionError(f"输入文件超过 {MAX_INPUT_BYTES} 字节上限：{requested}")
    try:
        digest = sha256_file(physical)
    except OSError as error:
        raise ToolExecutionError(f"输入文件无法读取：{requested}") from error
    return InputFile(
        path=physical.relative_to(root).as_posix(),
        sha256=digest,
        size_bytes=size,
        kind=classify(physical),
    )


async def freeze_inputs(root: Path, requested: list[str]) -> InputSet:
    """在 Task 创建时冻结输入集。"""

    if not requested:
        raise ToolExecutionError("输入集不能为空")
    if len(requested) > MAX_INPUT_FILES:
        raise ToolExecutionError(f"输入文件过多（上限 {MAX_INPUT_FILES}）")
    files = await asyncio.to_thread(lambda: tuple(describe_file(root, item) for item in requested))
    return InputSet(files=tuple(item.as_model() for item in files))


def verify_inputs(root: Path, frozen: InputSet) -> None:
    """运行中复核输入集；任一文件被替换/删除即抛 `input_changed`。"""

    for item in frozen.files:
        try:
            current = describe_file(root, item.path)
        except (ToolExecutionError, ToolPermissionError) as error:
            raise InputChangedError(
                f"冻结的输入 {item.path} 已不可用（{error}）；任务按输入变化终止"
            ) from error
        if current.sha256 != item.sha256:
            raise InputChangedError(
                f"冻结的输入 {item.path} 内容已变化（原 {item.sha256[:12]}…，"
                f"现 {current.sha256[:12]}…）；任务按输入变化终止"
            )


__all__ = [
    "MAX_INPUT_FILES",
    "FrozenInput",
    "InputChangedError",
    "InputFile",
    "InputSet",
    "classify",
    "describe_file",
    "freeze_inputs",
    "sha256_file",
    "verify_inputs",
]
