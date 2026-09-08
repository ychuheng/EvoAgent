"""只允许为当前 Run 创建新 Artifact 的窄写入工具。"""

import json
from uuid import UUID

from pydantic import Field

from evoagent.core.models import ContractModel, ToolRisk
from evoagent.tools.base import BaseTool
from evoagent.trace.artifacts import ArtifactService


class ArtifactWriteArguments(ContractModel):
    name: str = Field(min_length=1, max_length=255)
    content: str = Field(max_length=1_000_000)
    artifact_type: str = Field(default="text/plain", min_length=1, max_length=64)


class ArtifactWriteTool(BaseTool[ArtifactWriteArguments]):
    name = "artifact_write"
    description = "为当前运行创建一个不可覆盖的新 Artifact 文件。"
    arguments_model = ArtifactWriteArguments
    risk = ToolRisk.R1
    has_side_effects = True
    parallel_safe = False
    implementation_version = "1"

    def __init__(self, run_id: UUID, service: ArtifactService) -> None:
        self._run_id = run_id
        self._service = service

    async def invoke(self, arguments: ArtifactWriteArguments) -> str:
        record = await self._service.create_unique(
            run_id=self._run_id,
            name=arguments.name,
            content=arguments.content.encode("utf-8"),
            artifact_type=arguments.artifact_type,
            attributes={"created_by": self.name},
        )
        return json.dumps(
            {"artifact_id": str(record.id), "uri": record.uri, "hash": record.content_hash},
            ensure_ascii=False,
        )
