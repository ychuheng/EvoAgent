"""Deterministic registration and discovery of available tools."""

from collections.abc import Iterable

from evoagent.core.models import ToolDefinition
from evoagent.tools.base import BaseTool, ToolInstance


class ToolRegistryError(Exception):
    """Base class for registry failures."""


class DuplicateToolError(ToolRegistryError):
    """Raised when a name is registered more than once."""


class ToolNotFoundError(ToolRegistryError):
    """Raised when a caller requests a tool that is not registered."""


class InvalidToolError(ToolRegistryError):
    """Raised when an object does not implement the BaseTool contract."""


class ToolRegistry:
    """Store tools by unique name and expose stable model definitions."""

    def __init__(self, tools: Iterable[ToolInstance] = ()) -> None:
        self._tools: dict[str, ToolInstance] = {}
        for tool in tools:
            self.register(tool)

    def register(self, tool: ToolInstance) -> None:
        """Register one valid tool, rejecting ambiguous duplicate names."""

        if not isinstance(tool, BaseTool):
            raise InvalidToolError("registered objects must inherit from BaseTool")
        definition = tool.definition()
        if definition.name in self._tools:
            raise DuplicateToolError(f"tool is already registered: {definition.name}")
        self._tools[definition.name] = tool

    def get(self, name: str) -> ToolInstance:
        """Look up a tool by its exact public name."""

        try:
            return self._tools[name]
        except KeyError as error:
            available = ", ".join(self.names) or "<none>"
            raise ToolNotFoundError(
                f"unknown tool: {name}; available tools: {available}"
            ) from error

    @property
    def names(self) -> tuple[str, ...]:
        """Return names in a deterministic order."""

        return tuple(sorted(self._tools))

    def definitions(self) -> tuple[ToolDefinition, ...]:
        """Return model-facing definitions in the same deterministic order."""

        return tuple(self._tools[name].definition() for name in self.names)

    def __contains__(self, name: object) -> bool:
        return name in self._tools

    def __len__(self) -> int:
        return len(self._tools)
