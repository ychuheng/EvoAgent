"""只能写入当前 Run Sandbox 的 UTF-8 文件工具。"""

from pydantic import Field

from evoagent.core.models import ContractModel, ToolRisk
from evoagent.tools.base import BaseTool
from evoagent.tools.sandbox import RunSandbox


class FileWriteArguments(ContractModel):
    path: str = Field(min_length=1, max_length=1_024)
    content: str = Field(max_length=1_000_000)
    overwrite: bool = False


class FileWriteTool(BaseTool[FileWriteArguments]):
    name = "file_write"
    description = "Write UTF-8 text inside the current run artifact directory."
    arguments_model = FileWriteArguments
    risk = ToolRisk.R1
    has_side_effects = True
    parallel_safe = False

    def __init__(self, sandbox: RunSandbox) -> None:
        self._sandbox = sandbox

    def effective_risk(self, arguments: FileWriteArguments) -> ToolRisk:
        return ToolRisk.R2 if arguments.overwrite else ToolRisk.R1

    async def invoke(self, arguments: FileWriteArguments) -> str:
        target = await self._sandbox.write_text(
            arguments.path, arguments.content, overwrite=arguments.overwrite
        )
        return target.relative_to(self._sandbox.root).as_posix()
