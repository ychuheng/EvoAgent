"""API 进程使用的只读工具能力目录，不承担实际工具执行。"""

from typing import Any

from pydantic import BaseModel

from evoagent.core.models import ToolRisk
from evoagent.tools.base import BaseTool
from evoagent.tools.builtin.artifact_write import ArtifactWriteArguments
from evoagent.tools.builtin.calculator import CalculatorArguments
from evoagent.tools.builtin.file_read import FileReadArguments
from evoagent.tools.builtin.web_fetch import WebFetchArguments
from evoagent.tools.builtin.web_search import WebSearchArguments
from evoagent.tools.registry import ToolRegistry


class ToolCatalogEntry(BaseTool):
    """复用真实参数 Schema 的元数据条目；调用它属于装配错误。"""

    implementation_version = "catalog-1"

    def __init__(
        self,
        *,
        name: str,
        description: str,
        arguments_model: type[BaseModel],
        risk: ToolRisk,
        has_side_effects: bool = False,
        parallel_safe: bool = True,
    ) -> None:
        self.name = name
        self.description = description
        self.arguments_model = arguments_model
        self.risk = risk
        self.has_side_effects = has_side_effects
        self.parallel_safe = parallel_safe

    async def invoke(self, arguments: Any) -> str:
        del arguments
        raise RuntimeError("tool catalog entries cannot be executed")


def default_skill_tool_catalog() -> ToolRegistry:
    """返回与默认 Worker Skill 白名单对应的验证目录。"""

    return ToolRegistry(
        (
            ToolCatalogEntry(
                name="calculator",
                description="安全计算表达式。",
                arguments_model=CalculatorArguments,
                risk=ToolRisk.R0,
            ),
            ToolCatalogEntry(
                name="file_read",
                description="读取 Workspace 内的文本文件。",
                arguments_model=FileReadArguments,
                risk=ToolRisk.R0,
            ),
            ToolCatalogEntry(
                name="web_fetch",
                description="读取公开网页文本。",
                arguments_model=WebFetchArguments,
                risk=ToolRisk.R0,
            ),
            ToolCatalogEntry(
                name="web_search",
                description="搜索公开网页。",
                arguments_model=WebSearchArguments,
                risk=ToolRisk.R0,
            ),
            ToolCatalogEntry(
                name="artifact_write",
                description="创建不可覆盖的 Run Artifact。",
                arguments_model=ArtifactWriteArguments,
                risk=ToolRisk.R1,
                has_side_effects=True,
                parallel_safe=False,
            ),
        )
    )
