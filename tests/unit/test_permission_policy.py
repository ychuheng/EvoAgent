from pathlib import Path
from uuid import uuid4

from evoagent.core.models import ToolRisk
from evoagent.tools.builtin.calculator import CalculatorArguments, CalculatorTool
from evoagent.tools.builtin.file_write import FileWriteArguments, FileWriteTool
from evoagent.tools.policy import PermissionPolicy, PolicyAction
from evoagent.tools.sandbox import RunSandbox


def test_policy_allows_low_risk_and_requires_approval_for_overwrite(tmp_path: Path) -> None:
    policy = PermissionPolicy()
    calculator = policy.evaluate(CalculatorTool(), CalculatorArguments(expression="1+1"))
    writer = FileWriteTool(RunSandbox(tmp_path, uuid4()))
    create = policy.evaluate(
        writer, FileWriteArguments(path="new.txt", content="new", overwrite=False)
    )
    overwrite = policy.evaluate(
        writer, FileWriteArguments(path="old.txt", content="new", overwrite=True)
    )

    assert calculator.action is PolicyAction.ALLOW
    assert create.effective_risk is ToolRisk.R1
    assert create.action is PolicyAction.ALLOW
    assert overwrite.effective_risk is ToolRisk.R2
    assert overwrite.action is PolicyAction.REQUIRE_APPROVAL


def test_policy_can_deny_tool_regardless_of_declared_risk() -> None:
    decision = PermissionPolicy(denied_tools=frozenset({"calculator"})).evaluate(
        CalculatorTool(), CalculatorArguments(expression="1+1")
    )

    assert decision.action is PolicyAction.DENY
