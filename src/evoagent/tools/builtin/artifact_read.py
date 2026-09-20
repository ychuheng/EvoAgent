"""只按不透明 Artifact ID 分页读取，不接受路径或其他 Run。"""

from uuid import UUID

from pydantic import Field

from evoagent.core.models import ContractModel, ToolRisk
from evoagent.tools.base import BaseTool


class ArtifactReadArguments(ContractModel):
    artifact_id: UUID
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=2000, ge=1, le=8000)


class ArtifactReadTool(BaseTool[ArtifactReadArguments]):
    name = "artifact_read"
    description = "按 ID 和字符偏移读取当前运行的归档输出，最大 8000 字符。"
    arguments_model = ArtifactReadArguments
    risk = ToolRisk.R0
    has_side_effects = False
    parallel_safe = True

    def __init__(self, store):
        self.store = store

    async def invoke(self, arguments):
        return await self.store.read(arguments.artifact_id, arguments.offset, arguments.limit)
