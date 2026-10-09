"""在已授权项目内精确编辑文件（实施计划 §7 E-01/E-02/E-03）。

三个工具分工：

- `edit_file`：单文件、按哈希前置条件与定位文本替换；支持 `dry_run` 先看 diff。
- `apply_patch`：多文件、全成功或全部回滚；先校验全部前置条件再写盘。
- `delete_file` / `move_file`：文件操作单独暴露，删除只允许单个文件，不做隐式递归。

三者都要求项目为 `read_write` 授权，且都是 `has_side_effects=True`，
因此走既有的 Policy/Approval/ToolEffect 账本（§7 E-05）。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from pydantic import Field, model_validator

from evoagent.core.models import ContractModel, ToolRisk
from evoagent.projects.editing import (
    EditConflictError,
    EditRequest,
    LineRange,
    apply_edits,
    delete_path,
    move_path,
    sha256_bytes,
)
from evoagent.projects.schema import (
    ProjectAuthorization,
    resolve_inside_root,
)
from evoagent.tools.base import BaseTool, ToolExecutionError, ToolPermissionError

MAX_PATCH_FILES = 20


class _ProjectWriteTool(BaseTool):
    """写工具共用的授权检查。"""

    has_side_effects = True
    parallel_safe = False
    risk = ToolRisk.R1

    def __init__(self, root: Path, *, authorization: ProjectAuthorization) -> None:
        self._root = root
        self._authorization = authorization

    def _require_write(self) -> None:
        if self._authorization is not ProjectAuthorization.READ_WRITE:
            raise ToolPermissionError(
                "项目当前是只读授权；请先在页面上把该项目改为可写授权再修改文件"
            )


class EditFileArguments(ContractModel):
    path: str = Field(min_length=1, max_length=4_096)
    replacement: str = Field(max_length=1_000_000)
    expected_sha256: str | None = Field(default=None, min_length=64, max_length=64)
    old_text: str | None = Field(default=None, max_length=1_000_000)
    start_line: int | None = Field(default=None, ge=1, le=10_000_000)
    end_line: int | None = Field(default=None, ge=1, le=10_000_000)
    create: bool = False
    dry_run: bool = False

    @model_validator(mode="after")
    def check_locator(self) -> EditFileArguments:
        if self.old_text is not None and self.start_line is not None:
            raise ValueError("old_text 与 start_line/end_line 只能给出一个")
        if (self.start_line is None) != (self.end_line is None):
            raise ValueError("start_line 与 end_line 必须同时给出")
        if not self.create and self.old_text is None and self.start_line is None:
            raise ValueError("修改已存在的文件必须给出 old_text 或 start_line/end_line")
        if self.expected_sha256 is not None:
            value = self.expected_sha256.strip().lower()
            if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
                raise ValueError("expected_sha256 必须是 64 位十六进制")
        return self

    def to_request(self) -> EditRequest:
        line_range = (
            LineRange(start=self.start_line, end=self.end_line)  # type: ignore[arg-type]
            if self.start_line is not None and self.end_line is not None
            else None
        )
        return EditRequest(
            path=self.path,
            replacement=self.replacement,
            expected_sha256=self.expected_sha256,
            old_text=self.old_text,
            line_range=line_range,
            create=self.create,
        )


class EditFileTool(_ProjectWriteTool, BaseTool[EditFileArguments]):
    name = "edit_file"
    description = (
        "Edit one file inside the authorized project. Require the file's current SHA-256 "
        "(or a unique old_text) so concurrent human edits are never overwritten. "
        "Use dry_run=true first to see the diff. Only available with read_write authorization."
    )
    arguments_model = EditFileArguments

    async def invoke(self, arguments: EditFileArguments) -> str:
        self._require_write()
        return await asyncio.to_thread(self._run, arguments)

    def _run(self, arguments: EditFileArguments) -> str:
        outcome = apply_edits(self._root, [arguments.to_request()], dry_run=arguments.dry_run)
        item = outcome.outcomes[0]
        header = [
            f"文件：{item.display}",
            f"模式：{'dry_run（未写盘）' if arguments.dry_run else 'applied'}",
            f"新建：{'是' if item.created else '否'}",
            f"增删：+{item.added_lines} / -{item.removed_lines}",
            f"行尾（前）：{_format_endings(item.line_endings_before)}",
            f"行尾（后）：{_format_endings(item.line_endings_after)}",
        ]
        if not arguments.dry_run:
            header.append(f"新内容 SHA-256：{item.new_sha256}")
        if item.diff_truncated:
            header.append("diff 已截断；请缩小改动范围")
        return "\n".join([*header, "", item.diff or "（无差异）"])


class PatchFileSpec(ContractModel):
    path: str = Field(min_length=1, max_length=4_096)
    replacement: str = Field(max_length=1_000_000)
    expected_sha256: str | None = Field(default=None, min_length=64, max_length=64)
    old_text: str | None = Field(default=None, max_length=1_000_000)
    start_line: int | None = Field(default=None, ge=1, le=10_000_000)
    end_line: int | None = Field(default=None, ge=1, le=10_000_000)
    create: bool = False


class ApplyPatchArguments(ContractModel):
    edits: list[PatchFileSpec] = Field(min_length=1, max_length=MAX_PATCH_FILES)
    dry_run: bool = False


class ApplyPatchTool(_ProjectWriteTool, BaseTool[ApplyPatchArguments]):
    name = "apply_patch"
    description = (
        "Apply a patch touching several files inside the authorized project. "
        "All preconditions are checked before anything is written; if one file fails, "
        "already written files are rolled back and the result is never reported as success."
    )
    arguments_model = ApplyPatchArguments

    async def invoke(self, arguments: ApplyPatchArguments) -> str:
        self._require_write()
        return await asyncio.to_thread(self._run, arguments)

    def _run(self, arguments: ApplyPatchArguments) -> str:
        requests = [
            EditRequest(
                path=item.path,
                replacement=item.replacement,
                expected_sha256=item.expected_sha256,
                old_text=item.old_text,
                line_range=(
                    LineRange(start=item.start_line, end=item.end_line)
                    if item.start_line is not None and item.end_line is not None
                    else None
                ),
                create=item.create,
            )
            for item in arguments.edits
        ]
        outcome = apply_edits(self._root, requests, dry_run=arguments.dry_run)
        lines = [
            f"模式：{'dry_run（未写盘）' if arguments.dry_run else 'applied'}",
            f"文件数：{len(outcome.outcomes)}",
        ]
        for item in outcome.outcomes:
            lines.append(
                f"- {item.display}：{'新建' if item.created else '修改'}，"
                f"+{item.added_lines}/-{item.removed_lines}"
            )
            lines.append(item.diff or "  （无差异）")
        return "\n".join(lines)


class DeleteFileArguments(ContractModel):
    path: str = Field(min_length=1, max_length=4_096)
    expected_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    dry_run: bool = False


class DeleteFileTool(_ProjectWriteTool, BaseTool[DeleteFileArguments]):
    name = "delete_file"
    description = (
        "Delete a single file inside the authorized project. Directories are refused, "
        "so a recursive wipe can never happen implicitly. Requires the original SHA-256 "
        "from file_read; a changed file must be read again before deletion."
    )
    arguments_model = DeleteFileArguments

    async def invoke(self, arguments: DeleteFileArguments) -> str:
        self._require_write()
        return await asyncio.to_thread(self._run, arguments)

    def _run(self, arguments: DeleteFileArguments) -> str:
        _lexical, physical = resolve_inside_root(self._root, arguments.path)
        if not physical.is_file():
            raise ToolExecutionError("目标不是一个普通文件")
        with physical.open("rb") as handle:
            raw = handle.read(8 * 1024 * 1024 + 1)
        if len(raw) > 8 * 1024 * 1024:
            raise ToolExecutionError("文件超过删除核对上限 8388608 字节；需要人工处理")
        actual = sha256_bytes(raw)
        if actual != arguments.expected_sha256:
            raise EditConflictError(
                f"{arguments.path} 的内容已变化（实际 {actual[:12]}…）；请重新读取后再删"
            )
        if arguments.dry_run:
            return f"文件：{arguments.path}\n模式：dry_run（未删除）"
        display = delete_path(self._root, arguments.path)
        return f"文件：{display}\n已删除"


class MoveFileArguments(ContractModel):
    source: str = Field(min_length=1, max_length=4_096)
    destination: str = Field(min_length=1, max_length=4_096)
    dry_run: bool = False


class MoveFileTool(_ProjectWriteTool, BaseTool[MoveFileArguments]):
    name = "move_file"
    description = (
        "Rename or move one file inside the authorized project. The destination must not "
        "exist, so an existing file is never overwritten silently."
    )
    arguments_model = MoveFileArguments

    async def invoke(self, arguments: MoveFileArguments) -> str:
        self._require_write()
        return await asyncio.to_thread(self._run, arguments)

    def _run(self, arguments: MoveFileArguments) -> str:
        if arguments.dry_run:
            return f"源：{arguments.source}\n目标：{arguments.destination}\n模式：dry_run（未移动）"
        source, destination = move_path(self._root, arguments.source, arguments.destination)
        return f"源：{source}\n目标：{destination}\n已移动"


def _format_endings(counts: dict[str, int]) -> str:
    return ", ".join(f"{name}={value}" for name, value in counts.items())


def project_edit_tools(root: Path, *, authorization: ProjectAuthorization) -> list[BaseTool]:
    """装配写工具；只读授权时返回空列表，模型连工具都看不到。"""

    if authorization is not ProjectAuthorization.READ_WRITE:
        return []
    return [
        EditFileTool(root, authorization=authorization),
        ApplyPatchTool(root, authorization=authorization),
        DeleteFileTool(root, authorization=authorization),
        MoveFileTool(root, authorization=authorization),
    ]
