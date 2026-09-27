"""M3 命令工具与审批档位的单元测试（实施计划 §8 X-03）。"""

from pathlib import Path

import pytest

from evoagent.core.models import ToolRisk
from evoagent.projects.schema import ProjectAuthorization
from evoagent.tools.builtin.project_command import (
    ProjectCommandTool,
    RunCommandArguments,
    project_command_tools,
)
from evoagent.tools.policy import PermissionPolicy, PolicyAction


def make_tool(root: Path) -> ProjectCommandTool:
    return ProjectCommandTool(
        root, allowlist=("python", "pytest"), timeout_seconds=30, output_bytes=4_096
    )


def test_command_tools_require_write_authorization_and_allowlist(tmp_path: Path) -> None:
    assert (
        project_command_tools(
            tmp_path,
            authorization=ProjectAuthorization.READ,
            allowlist=("python",),
            timeout_seconds=30,
            output_bytes=1_024,
        )
        == []
    )
    assert (
        project_command_tools(
            tmp_path,
            authorization=ProjectAuthorization.READ_WRITE,
            allowlist=(),
            timeout_seconds=30,
            output_bytes=1_024,
        )
        == []
    )
    tools = project_command_tools(
        tmp_path,
        authorization=ProjectAuthorization.READ_WRITE,
        allowlist=("python",),
        timeout_seconds=30,
        output_bytes=1_024,
    )
    assert [tool.name for tool in tools] == ["run_command"]


def test_network_command_is_escalated_to_explicit_approval(tmp_path: Path) -> None:
    tool = make_tool(tmp_path)
    policy = PermissionPolicy()

    offline = policy.evaluate(tool, RunCommandArguments(argv=("python", "-V"), allow_network=False))
    online = policy.evaluate(tool, RunCommandArguments(argv=("python", "-V"), allow_network=True))

    assert offline.effective_risk is ToolRisk.R1
    assert offline.action is PolicyAction.ALLOW
    assert online.effective_risk is ToolRisk.R2
    assert online.action is PolicyAction.REQUIRE_APPROVAL


def test_command_binding_records_allowlist_and_environment_keys(tmp_path: Path) -> None:
    tool = ProjectCommandTool(
        tmp_path,
        allowlist=("python",),
        timeout_seconds=30,
        output_bytes=1_024,
        environment={"PYTHONPATH": "src"},
    )

    binding = tool.execution_binding()

    assert binding["kind"] == "project_command"
    assert binding["allowlist"] == ["python"]
    # 只记录键名，不把值写进绑定（值可能是部署方私有配置）。
    assert binding["environment"] == ["PYTHONPATH"]


@pytest.mark.asyncio
async def test_run_command_returns_failure_evidence_instead_of_raising(tmp_path: Path) -> None:
    """退出码非 0 必须作为结构化证据返回，否则循环无法据失败修正。"""

    import json
    import sys

    tool = ProjectCommandTool(
        tmp_path,
        allowlist=(Path(sys.executable).name,),
        timeout_seconds=30,
        output_bytes=4_096,
    )

    payload = json.loads(
        await tool.invoke(
            RunCommandArguments(
                argv=(sys.executable, "-c", "import sys\nsys.stderr.write('bad')\nsys.exit(2)")
            )
        )
    )

    assert payload["return_code"] == 2
    assert payload["stderr"].strip() == "bad"
    assert payload["network"] == "denied"
    assert payload["timed_out"] is False
