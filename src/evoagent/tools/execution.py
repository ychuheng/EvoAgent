"""ToolExecutor 可选执行中间件的稳定协议。"""

from dataclasses import dataclass
from typing import Any, Protocol

from pydantic import BaseModel

from evoagent.core.models import ToolCall, ToolResult
from evoagent.tools.base import ToolInstance


@dataclass(frozen=True, slots=True)
class ToolExecutionDirective:
    token: Any = None
    result: ToolResult | None = None


class ToolExecutionMiddleware(Protocol):
    async def before(
        self,
        call: ToolCall,
        tool: ToolInstance,
        arguments: BaseModel,
    ) -> ToolExecutionDirective: ...

    async def after_success(self, token: Any, content: str) -> None: ...

    async def after_failure(self, token: Any, error_code: str, message: str) -> None: ...
