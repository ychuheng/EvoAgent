"""API 进程使用的只读工具能力目录，不承担实际工具执行。"""

from typing import Any

from pydantic import BaseModel

from evoagent.core.models import ToolRisk
from evoagent.tools.base import BaseTool
from evoagent.tools.builtin.artifact_write import ArtifactWriteArguments
from evoagent.tools.builtin.calculator import CalculatorArguments
from evoagent.tools.builtin.find_files import FindFilesArguments
from evoagent.tools.builtin.list_dir import ListDirArguments
from evoagent.tools.builtin.project_command import RunCommandArguments
from evoagent.tools.builtin.project_edit import (
    ApplyPatchArguments,
    DeleteFileArguments,
    EditFileArguments,
    MoveFileArguments,
)
from evoagent.tools.builtin.project_file_read import ProjectFileReadArguments
from evoagent.tools.builtin.search_text import SearchTextArguments
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
                description="读取已授权项目内的文本文件（可按行范围分段读取）。",
                arguments_model=ProjectFileReadArguments,
                risk=ToolRisk.R0,
            ),
            ToolCatalogEntry(
                name="list_dir",
                description="列出已授权项目内目录的直接子项。",
                arguments_model=ListDirArguments,
                risk=ToolRisk.R0,
            ),
            ToolCatalogEntry(
                name="find_files",
                description="按模式在已授权项目内查找文件。",
                arguments_model=FindFilesArguments,
                risk=ToolRisk.R0,
            ),
            ToolCatalogEntry(
                name="search_text",
                description="在已授权项目内按内容搜索文本。",
                arguments_model=SearchTextArguments,
                risk=ToolRisk.R0,
            ),
            ToolCatalogEntry(
                name="edit_file",
                description="按哈希前置条件精确修改已授权项目内的单个文件。",
                arguments_model=EditFileArguments,
                risk=ToolRisk.R1,
                has_side_effects=True,
                parallel_safe=False,
            ),
            ToolCatalogEntry(
                name="apply_patch",
                description="一次修改多个文件；全部前置条件通过才写盘，否则整体回滚。",
                arguments_model=ApplyPatchArguments,
                risk=ToolRisk.R1,
                has_side_effects=True,
                parallel_safe=False,
            ),
            ToolCatalogEntry(
                name="delete_file",
                description="删除已授权项目内的单个文件（拒绝目录）。",
                arguments_model=DeleteFileArguments,
                risk=ToolRisk.R1,
                has_side_effects=True,
                parallel_safe=False,
            ),
            ToolCatalogEntry(
                name="move_file",
                description="重命名或移动已授权项目内的单个文件（不覆盖已存在目标）。",
                arguments_model=MoveFileArguments,
                risk=ToolRisk.R1,
                has_side_effects=True,
                parallel_safe=False,
            ),
            ToolCatalogEntry(
                name="run_command",
                description="在已授权项目内运行白名单内的结构化 argv；默认离线。",
                arguments_model=RunCommandArguments,
                risk=ToolRisk.R1,
                has_side_effects=True,
                parallel_safe=False,
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
