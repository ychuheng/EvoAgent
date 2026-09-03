import pytest

from evoagent.tools.builtin.calculator import CalculatorTool
from evoagent.tools.registry import (
    DuplicateToolError,
    InvalidToolError,
    ToolNotFoundError,
    ToolRegistry,
)


def test_registry_registers_and_returns_tool() -> None:
    calculator = CalculatorTool()
    registry = ToolRegistry()

    registry.register(calculator)

    assert len(registry) == 1
    assert "calculator" in registry
    assert registry.get("calculator") is calculator


def test_registry_rejects_duplicate_name() -> None:
    registry = ToolRegistry([CalculatorTool()])

    with pytest.raises(DuplicateToolError, match="calculator"):
        registry.register(CalculatorTool())


def test_registry_reports_unknown_tool_and_available_names() -> None:
    registry = ToolRegistry([CalculatorTool()])

    with pytest.raises(ToolNotFoundError, match="available tools: calculator"):
        registry.get("missing")


def test_registry_exposes_deterministic_json_schema() -> None:
    registry = ToolRegistry([CalculatorTool()])

    definition = registry.definitions()[0]

    assert definition.name == "calculator"
    assert definition.parameters["type"] == "object"
    assert definition.parameters["properties"]["expression"]["type"] == "string"
    assert definition.parameters["required"] == ["expression"]


def test_registry_rejects_objects_outside_tool_contract() -> None:
    registry = ToolRegistry()

    with pytest.raises(InvalidToolError, match="BaseTool"):
        registry.register(object())  # type: ignore[arg-type]
