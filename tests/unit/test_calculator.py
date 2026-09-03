import pytest
from pydantic import ValidationError

from evoagent.core.models import ToolRisk
from evoagent.tools.base import ToolExecutionError
from evoagent.tools.builtin.calculator import CalculatorArguments, CalculatorTool


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("12 * (3 + 4)", "84"),
        ("10 / 4", "2.5"),
        ("9 // 2", "4"),
        ("2 ** 8", "256"),
        ("-3 + +5", "2"),
    ],
)
async def test_calculator_evaluates_safe_arithmetic(expression: str, expected: str) -> None:
    tool = CalculatorTool()

    result = await tool.invoke(CalculatorArguments(expression=expression))

    assert result == expected


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "expression",
    [
        "__import__('os').system('echo unsafe')",
        "open('secret.txt').read()",
        "value + 1",
        "[1, 2, 3]",
        "True + 1",
        "2 ** 11",
        "10 ** 100",
    ],
)
async def test_calculator_rejects_unsafe_or_excessive_expressions(expression: str) -> None:
    tool = CalculatorTool()

    with pytest.raises(ToolExecutionError):
        await tool.invoke(CalculatorArguments(expression=expression))


@pytest.mark.asyncio
async def test_calculator_converts_division_by_zero_to_tool_error() -> None:
    with pytest.raises(ToolExecutionError, match="division by zero"):
        await CalculatorTool().invoke(CalculatorArguments(expression="1 / 0"))


def test_calculator_argument_validation_strips_text_and_rejects_blank() -> None:
    assert CalculatorArguments(expression=" 1 + 1 ").expression == "1 + 1"

    with pytest.raises(ValidationError):
        CalculatorArguments(expression="   ")


def test_calculator_declares_safe_metadata() -> None:
    tool = CalculatorTool()

    assert tool.risk is ToolRisk.R0
    assert tool.has_side_effects is False
    assert tool.parallel_safe is True
