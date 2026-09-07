"""受 allowlist 和 argv 边界约束的 Shell 工具。"""

import json

from pydantic import Field

from evoagent.core.models import ContractModel, ToolRisk
from evoagent.tools.base import BaseTool, ToolExecutionError
from evoagent.tools.sandbox import ShellSandbox


class ShellArguments(ContractModel):
    argv: tuple[str, ...] = Field(min_length=1, max_length=64)


class ShellTool(BaseTool[ShellArguments]):
    name = "shell"
    description = "Run an approved executable with an argv list inside the workspace."
    arguments_model = ShellArguments
    risk = ToolRisk.R2
    has_side_effects = True
    parallel_safe = False

    def __init__(self, sandbox: ShellSandbox) -> None:
        self._sandbox = sandbox

    async def invoke(self, arguments: ShellArguments) -> str:
        result = await self._sandbox.run(arguments.argv)
        if result.return_code != 0:
            raise ToolExecutionError(f"command exited with {result.return_code}: {result.stderr}")
        return json.dumps(
            {"return_code": result.return_code, "stdout": result.stdout},
            ensure_ascii=False,
        )
