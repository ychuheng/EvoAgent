"""受 allowlist 和 argv 边界约束的 Shell 工具。"""

import json
from uuid import UUID

from pydantic import Field

from evoagent.core.models import ContractModel, ToolRisk
from evoagent.sandbox.schema import SandboxExecutor
from evoagent.tools.base import BaseTool, ToolExecutionError


class ShellArguments(ContractModel):
    argv: tuple[str, ...] = Field(min_length=1, max_length=64)
    input_artifact_ids: tuple[UUID, ...] = Field(default=(), max_length=32)


class ShellTool(BaseTool[ShellArguments]):
    name = "shell"
    description = (
        "Run an approved argv in an isolated Docker sandbox; "
        "inputs are mounted at /input/<artifact UUID>."
    )
    arguments_model = ShellArguments
    risk = ToolRisk.R2
    has_side_effects = True
    parallel_safe = False

    def __init__(self, sandbox: SandboxExecutor) -> None:
        self._sandbox = sandbox
        self.implementation_version = getattr(
            sandbox, "implementation_version", "legacy-fixture-v1"
        )

    def execution_binding(self):
        if hasattr(self._sandbox, "profile_hash"):
            return {
                "kind": "sandbox",
                "profile": self._sandbox.profile,
                "profile_hash": self._sandbox.profile_hash,
            }
        return {}

    async def invoke(self, arguments: ShellArguments) -> str:
        result = (
            await self._sandbox.run(arguments.argv, arguments.input_artifact_ids)
            if arguments.input_artifact_ids
            else await self._sandbox.run(arguments.argv)
        )
        if result.return_code != 0:
            raise ToolExecutionError(f"command exited with {result.return_code}: {result.stderr}")
        return json.dumps(
            {
                "return_code": result.return_code,
                "stdout": result.stdout,
                **({"artifacts": result.artifacts} if result.artifacts else {}),
            },
            ensure_ascii=False,
        )
