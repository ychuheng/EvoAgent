"""所有 EvoAgent 工具都必须实现的基础抽象。"""

from abc import ABC, abstractmethod
from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, JsonValue

from evoagent.core.models import ToolDefinition, ToolRisk


class BaseTool[ArgumentsT: BaseModel](ABC):
    """一种以后可以由 ToolExecutor 调用的类型化能力。

    ToolRegistry 只存储这些对象并公开其定义。对于模型生成的请求，
    它不会调用 ``validate_arguments`` 或 ``invoke``。
    """

    name: str
    description: str
    arguments_model: type[ArgumentsT]
    risk: ToolRisk
    has_side_effects: bool
    parallel_safe: bool

    def definition(self) -> ToolDefinition:
        """构建向模型公开的、与具体模型服务无关的工具定义。"""

        return ToolDefinition(
            name=self.name,
            description=self.description,
            parameters=self.arguments_model.model_json_schema(),
        )

    def validate_arguments(self, arguments: Mapping[str, JsonValue]) -> ArgumentsT:
        """将不可信的原始参数转换为该工具的类型化输入模型。"""

        return self.arguments_model.model_validate(dict(arguments))

    @abstractmethod
    async def invoke(self, arguments: ArgumentsT) -> str:
        """使用已经校验的参数执行工具功能。"""


class ToolError(Exception):
    """工具实现主动抛出的预期失败的基类。"""


class ToolExecutionError(ToolError):
    """工具选择正确，但无法产生有效结果。"""


ToolInstance = BaseTool[Any]
