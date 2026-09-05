"""只能读取 Workspace 内普通 UTF-8 文件的工具。"""

import asyncio
from pathlib import Path

from pydantic import Field, field_validator

from evoagent.core.models import ContractModel, ToolRisk
from evoagent.tools.base import BaseTool, ToolExecutionError
from evoagent.tools.guards import WorkspaceGuard


class FileReadArguments(ContractModel):
    path: str = Field(min_length=1, max_length=4_096)

    @field_validator("path")
    @classmethod
    def normalize_path(cls, value: str) -> str:
        path = value.strip()
        if not path:
            raise ValueError("path cannot be blank")
        return path


class FileReadTool(BaseTool[FileReadArguments]):
    """通过 WorkspaceGuard 解析路径后读取大小受限的文本文件。"""

    name = "file_read"
    description = "Read a UTF-8 text file located inside the configured workspace."
    arguments_model = FileReadArguments
    risk = ToolRisk.R0
    has_side_effects = False
    parallel_safe = True

    def __init__(self, workspace: Path, *, max_bytes: int = 1_000_000) -> None:
        if max_bytes < 1:
            raise ValueError("max_bytes must be positive")
        self._guard = WorkspaceGuard(workspace)
        self._max_bytes = max_bytes

    async def invoke(self, arguments: FileReadArguments) -> str:
        path = self._guard.resolve_file(arguments.path)
        return await asyncio.to_thread(self._read_utf8, path)

    def _read_utf8(self, path: Path) -> str:
        try:
            with path.open("rb") as file:
                content = file.read(self._max_bytes + 1)
        except OSError as error:
            raise ToolExecutionError("file could not be read") from error
        if len(content) > self._max_bytes:
            raise ToolExecutionError(f"file exceeds the {self._max_bytes} byte limit")
        try:
            return content.decode("utf-8")
        except UnicodeDecodeError as error:
            raise ToolExecutionError("file is not valid UTF-8 text") from error
