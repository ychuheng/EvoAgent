"""只能写入当前 Run 输出空间、并登记为可下载产物的 UTF-8 文件工具。"""

import json

from pydantic import Field

from evoagent.core.models import ContractModel, ToolRisk
from evoagent.tools.base import BaseTool
from evoagent.tools.sandbox import RunSandbox
from evoagent.trace.artifacts import ArtifactService


class FileWriteArguments(ContractModel):
    path: str = Field(min_length=1, max_length=1_024)
    content: str = Field(max_length=1_000_000)
    overwrite: bool = False


# 只做"预览/下载媒体类型"的最小推断；未知后缀按纯文本处理，不影响下载。
_CONTENT_TYPES = {
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".txt": "text/plain",
    ".json": "application/json",
    ".csv": "text/csv",
    ".py": "text/x-python",
    ".log": "text/plain",
    ".yaml": "application/yaml",
    ".yml": "application/yaml",
    ".html": "text/html",
    ".xml": "application/xml",
}


def content_type_for(path: str) -> str:
    lowered = path.lower()
    for suffix, content_type in _CONTENT_TYPES.items():
        if lowered.endswith(suffix):
            return content_type
    return "text/plain"


class FileWriteTool(BaseTool[FileWriteArguments]):
    """把文本写进本次 Run 的输出空间，并登记成可预览、可下载、可核对哈希的产物。

    与 `artifact_write` 的分工：

    - `file_write`：**按路径**写文件（`reports/summary.md` 这类子路径也支持），同名文件可用
      `overwrite=true` 覆盖；登记记录随内容**就地更新**，因此同一个文件在页面上只有一条产物。
    - `artifact_write`：创建**不可覆盖**的产物（同名第二次会被拒绝），适合"必须交付、不能被
      后续步骤悄悄替换"的文件。

    两者产出的都是 F-04 的产物出口：网页能看到预览与登记哈希，下载响应头带 SHA-256 供核对。
    """

    name = "file_write"
    description = (
        "Write a UTF-8 file inside the current run's output space and register it as a "
        "downloadable artifact (preview + SHA-256 in the web UI). Use it for the file the "
        "user asked for; pass overwrite=true to replace the same path (the artifact record "
        "is updated in place). Use artifact_write instead when the file must never be replaced."
    )
    arguments_model = FileWriteArguments
    risk = ToolRisk.R1
    has_side_effects = True
    parallel_safe = False

    def __init__(self, sandbox: RunSandbox, artifacts: ArtifactService | None = None) -> None:
        self._sandbox = sandbox
        self._artifacts = artifacts

    def effective_risk(self, arguments: FileWriteArguments) -> ToolRisk:
        return ToolRisk.R2 if arguments.overwrite else ToolRisk.R1

    async def invoke(self, arguments: FileWriteArguments) -> str:
        target = await self._sandbox.write_text(
            arguments.path, arguments.content, overwrite=arguments.overwrite
        )
        relative = target.relative_to(self._sandbox.root).as_posix()
        content = arguments.content.encode("utf-8")
        payload: dict[str, object] = {
            "path": relative,
            "bytes": len(content),
            "downloadable": self._artifacts is not None,
        }
        if self._artifacts is None:
            return json.dumps(payload, ensure_ascii=False)
        record = await self._artifacts.create_or_replace(
            run_id=self._sandbox.run_id,
            name=relative,
            content=content,
            artifact_type=content_type_for(relative),
            attributes={"created_by": self.name, "content_type": content_type_for(relative)},
        )
        payload["artifact_id"] = str(record.id)
        payload["content_hash"] = record.content_hash
        return json.dumps(payload, ensure_ascii=False)
