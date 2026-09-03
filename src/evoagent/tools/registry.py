"""以确定性顺序注册和发现可用工具。"""

from collections.abc import Iterable

from evoagent.core.models import ToolDefinition
from evoagent.tools.base import BaseTool, ToolInstance


class ToolRegistryError(Exception):
    """工具注册表错误的基类。"""


class DuplicateToolError(ToolRegistryError):
    """同一个工具名称被重复注册时抛出。"""


class ToolNotFoundError(ToolRegistryError):
    """调用方请求了未注册的工具时抛出。"""


class InvalidToolError(ToolRegistryError):
    """对象没有实现 BaseTool 契约时抛出。"""


class ToolRegistry:
    """按唯一名称存储工具，并公开顺序稳定的模型工具定义。"""

    def __init__(self, tools: Iterable[ToolInstance] = ()) -> None:
        self._tools: dict[str, ToolInstance] = {}
        for tool in tools:
            self.register(tool)

    def register(self, tool: ToolInstance) -> None:
        """注册一个有效工具，并拒绝含义不明确的重名工具。"""

        if not isinstance(tool, BaseTool):
            raise InvalidToolError("registered objects must inherit from BaseTool")
        definition = tool.definition()
        if definition.name in self._tools:
            raise DuplicateToolError(f"tool is already registered: {definition.name}")
        self._tools[definition.name] = tool

    def get(self, name: str) -> ToolInstance:
        """根据工具公开的准确名称查找工具。"""

        try:
            return self._tools[name]
        except KeyError as error:
            available = ", ".join(self.names) or "<none>"
            raise ToolNotFoundError(
                f"unknown tool: {name}; available tools: {available}"
            ) from error

    @property
    def names(self) -> tuple[str, ...]:
        """以确定性顺序返回工具名称。"""

        return tuple(sorted(self._tools))

    def definitions(self) -> tuple[ToolDefinition, ...]:
        """以相同的确定性顺序返回面向模型的工具定义。"""

        return tuple(self._tools[name].definition() for name in self.names)

    def __contains__(self, name: object) -> bool:
        return name in self._tools

    def __len__(self) -> int:
        return len(self._tools)
