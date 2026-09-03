"""一个不会执行任意 Python 代码的小型算术工具。"""

import ast
import math
import operator
from collections.abc import Callable
from typing import ClassVar

from pydantic import Field, field_validator

from evoagent.core.models import ContractModel, ToolRisk
from evoagent.tools.base import BaseTool, ToolExecutionError

Number = int | float
BinaryOperator = Callable[[Number, Number], Number]
UnaryOperator = Callable[[Number], Number]


class CalculatorArguments(ContractModel):
    expression: str = Field(min_length=1, max_length=200)

    @field_validator("expression")
    @classmethod
    def normalize_expression(cls, value: str) -> str:
        expression = value.strip()
        if not expression:
            raise ValueError("expression cannot be blank")
        return expression


class CalculatorTool(BaseTool[CalculatorArguments]):
    """通过 ``ast`` 计算受到严格限制的算术表达式。"""

    name = "calculator"
    description = "Calculate an arithmetic expression using numbers and safe operators."
    arguments_model = CalculatorArguments
    risk = ToolRisk.R0
    has_side_effects = False
    parallel_safe = True

    _MAX_AST_NODES: ClassVar[int] = 64
    _MAX_ABS_EXPONENT: ClassVar[int] = 10
    _MAX_ABS_RESULT: ClassVar[int] = 10**100
    _BINARY_OPERATORS: ClassVar[dict[type[ast.operator], BinaryOperator]] = {
        ast.Add: operator.add,
        ast.Sub: operator.sub,
        ast.Mult: operator.mul,
        ast.Div: operator.truediv,
        ast.FloorDiv: operator.floordiv,
        ast.Mod: operator.mod,
        ast.Pow: operator.pow,
    }
    _UNARY_OPERATORS: ClassVar[dict[type[ast.unaryop], UnaryOperator]] = {
        ast.UAdd: operator.pos,
        ast.USub: operator.neg,
    }

    async def invoke(self, arguments: CalculatorArguments) -> str:
        try:
            tree = ast.parse(arguments.expression, mode="eval")
        except (SyntaxError, ValueError) as error:
            raise ToolExecutionError("invalid arithmetic expression") from error

        if sum(1 for _ in ast.walk(tree)) > self._MAX_AST_NODES:
            raise ToolExecutionError("arithmetic expression is too complex")

        try:
            result = self._evaluate(tree.body)
        except ZeroDivisionError as error:
            raise ToolExecutionError("division by zero") from error
        return str(result)

    def _evaluate(self, node: ast.AST) -> Number:
        if isinstance(node, ast.Constant):
            if type(node.value) not in (int, float):
                raise ToolExecutionError("only real numeric literals are allowed")
            return self._check_result(node.value)

        if isinstance(node, ast.UnaryOp):
            operation = self._UNARY_OPERATORS.get(type(node.op))
            if operation is None:
                raise ToolExecutionError("unary operator is not allowed")
            return self._check_result(operation(self._evaluate(node.operand)))

        if isinstance(node, ast.BinOp):
            operation = self._BINARY_OPERATORS.get(type(node.op))
            if operation is None:
                raise ToolExecutionError("binary operator is not allowed")
            left = self._evaluate(node.left)
            right = self._evaluate(node.right)
            if isinstance(node.op, ast.Pow) and (
                type(right) is not int or abs(right) > self._MAX_ABS_EXPONENT
            ):
                raise ToolExecutionError("exponent must be an integer between -10 and 10")
            return self._check_result(operation(left, right))

        raise ToolExecutionError(f"expression element is not allowed: {type(node).__name__}")

    def _check_result(self, value: Number) -> Number:
        if type(value) not in (int, float):
            raise ToolExecutionError("expression must produce a real number")
        if isinstance(value, float) and not math.isfinite(value):
            raise ToolExecutionError("result must be finite")
        if abs(value) > self._MAX_ABS_RESULT:
            raise ToolExecutionError("result is too large")
        return value
