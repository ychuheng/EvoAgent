"""在已授权项目内运行检查/测试命令（实施计划 §8 X-02/X-03/X-04）。

`run_command` 只接受结构化 argv：

- 默认使用 Linux seccomp 阻断网络；`allow_network=true` 的风险被提升到 R2，
  因此**必须**经过一次独立的人工审批（X-03：一次测试批准不带来安装或推送权）。
- 输出按上限截断，超时强杀；stdout/stderr/退出码/耗时/截断标记都结构化返回，
  让模型能据失败继续修正（X-04）。
- 只有 `read_write` 授权的项目才会注册这个工具。
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import Field

from evoagent.core.models import ContractModel, ToolRisk
from evoagent.projects.commands import (
    DEFAULT_MAX_PROCESSES,
    CommandSpec,
    command_category,
    run_command,
)
from evoagent.projects.schema import ProjectAuthorization
from evoagent.tools.base import BaseTool


class RunCommandArguments(ContractModel):
    argv: tuple[str, ...] = Field(min_length=1, max_length=64)
    cwd: str = Field(default=".", min_length=1, max_length=4_096)
    allow_network: bool = False


class ProjectCommandTool(BaseTool[RunCommandArguments]):
    name = "run_command"
    description = (
        "Run an allowlisted program inside the authorized project as a structured argv "
        "(no shell). Working directory must be inside the project. Proxy variables are "
        "blocked by Linux seccomp by default; "
        "allow_network=true needs a separate approval."
    )
    arguments_model = RunCommandArguments
    risk = ToolRisk.R1
    has_side_effects = True
    parallel_safe = False
    dedupe_by_arguments = False

    def __init__(
        self,
        root: Path,
        *,
        allowlist: tuple[str, ...],
        timeout_seconds: float,
        output_bytes: int,
        memory_bytes: int = 1_073_741_824,
        max_processes: int = DEFAULT_MAX_PROCESSES,
        environment: dict[str, str] | None = None,
    ) -> None:
        self._root = root
        self._allowlist = allowlist
        self._timeout_seconds = timeout_seconds
        self._output_bytes = output_bytes
        self._memory_bytes = memory_bytes
        self._max_processes = max_processes
        self._environment = dict(environment or {})

    def effective_risk(self, arguments: RunCommandArguments) -> ToolRisk:
        # Only recognizable offline checks are auto-approved. Build, install,
        # arbitrary interpreters and networking require a decision per call.
        category = command_category(arguments.argv)
        return ToolRisk.R1 if category == "check" and not arguments.allow_network else ToolRisk.R2

    def execution_binding(self) -> dict[str, object]:
        return {
            "kind": "project_command",
            "allowlist": sorted(self._allowlist),
            "environment": sorted(self._environment),
            "output_bytes": self._output_bytes,
            "memory_bytes": self._memory_bytes,
            "max_processes": self._max_processes,
            "timeout_seconds": self._timeout_seconds,
        }

    async def invoke(self, arguments: RunCommandArguments) -> str:
        outcome = await run_command(
            self._root,
            CommandSpec(
                argv=arguments.argv,
                cwd=arguments.cwd,
                allow_network=arguments.allow_network,
            ),
            allowlist=self._allowlist,
            timeout_seconds=self._timeout_seconds,
            output_limit=self._output_bytes,
            memory_limit_bytes=self._memory_bytes,
            max_processes=self._max_processes,
            environment_extra=self._environment,
        )
        payload = {
            "program": outcome.program,
            "argv": list(arguments.argv),
            "cwd": outcome.cwd,
            "network": "allowed" if arguments.allow_network else "isolated",
            "category": command_category(arguments.argv),
            "return_code": outcome.return_code,
            "timed_out": outcome.timed_out,
            "process_limit_exceeded": outcome.process_limit_exceeded,
            "duration_seconds": outcome.duration_seconds,
            "stdout": outcome.stdout,
            "stderr": outcome.stderr,
            "stdout_truncated": outcome.stdout_truncated,
            "stderr_truncated": outcome.stderr_truncated,
        }
        # 退出码非 0 或超时属于"失败证据"，必须原样返回给模型而不是抛错，
        # 否则循环只会看到一句异常文本，无法据失败修正。
        return json.dumps(payload, ensure_ascii=False, indent=2)


def project_command_tools(
    root: Path,
    *,
    authorization: ProjectAuthorization,
    allowlist: tuple[str, ...],
    timeout_seconds: float,
    output_bytes: int,
    memory_bytes: int = 1_073_741_824,
    max_processes: int = DEFAULT_MAX_PROCESSES,
    environment: dict[str, str] | None = None,
) -> list[BaseTool]:
    """装配命令工具；只读授权或空白名单时返回空列表。"""

    if authorization is not ProjectAuthorization.READ_WRITE or not allowlist:
        return []
    return [
        ProjectCommandTool(
            root,
            allowlist=allowlist,
            timeout_seconds=timeout_seconds,
            output_bytes=output_bytes,
            memory_bytes=memory_bytes,
            max_processes=max_processes,
            environment=environment,
        )
    ]
