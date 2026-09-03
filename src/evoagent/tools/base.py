"""Base abstraction implemented by every EvoAgent tool."""

from abc import ABC, abstractmethod
from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, JsonValue

from evoagent.core.models import ToolDefinition, ToolRisk


class BaseTool[ArgumentsT: BaseModel](ABC):
    """A typed capability that may later be invoked by ToolExecutor.

    ToolRegistry only stores these objects and exposes their definitions. It does
    not call ``validate_arguments`` or ``invoke`` for a model-generated request.
    """

    name: str
    description: str
    arguments_model: type[ArgumentsT]
    risk: ToolRisk
    has_side_effects: bool
    parallel_safe: bool

    def definition(self) -> ToolDefinition:
        """Build the provider-neutral definition shown to a model."""

        return ToolDefinition(
            name=self.name,
            description=self.description,
            parameters=self.arguments_model.model_json_schema(),
        )

    def validate_arguments(self, arguments: Mapping[str, JsonValue]) -> ArgumentsT:
        """Convert untrusted raw arguments into this tool's typed input model."""

        return self.arguments_model.model_validate(dict(arguments))

    @abstractmethod
    async def invoke(self, arguments: ArgumentsT) -> str:
        """Perform the tool's work with already validated arguments."""


class ToolError(Exception):
    """Base class for expected failures raised by a tool implementation."""


class ToolExecutionError(ToolError):
    """The tool was selected correctly but could not produce a valid result."""


ToolInstance = BaseTool[Any]
