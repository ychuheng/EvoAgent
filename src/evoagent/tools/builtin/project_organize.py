"""M5 F-05：目录整理工具——先出计划与冲突清单，dry-run 后再执行。

与 `move_file` 的分工：

- `move_file` 处理单个文件的移动/重命名，一次一个、拒绝覆盖；
- `organize_files` 处理**一批**整理，先给出逐条计划与冲突清单，`dry_run` 时只返回计划；
  执行时中途失败会逆序回滚，并如实报告回滚了哪些文件。

**删除不在这个工具里**：删除走 `delete_file`，保持"删除单独审批"。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from pydantic import Field

from evoagent.core.models import ContractModel, ToolRisk
from evoagent.projects.organize import MovePlan, MoveRule, apply_plan, plan_moves
from evoagent.projects.schema import ProjectAuthorization
from evoagent.tools.base import BaseTool, ToolPermissionError

MAX_ITEMS = 200


class OrganizeItem(ContractModel):
    source: str = Field(min_length=1, max_length=4_096)
    destination: str = Field(min_length=1, max_length=4_096)


class OrganizeFilesArguments(ContractModel):
    rules: list[OrganizeItem] = Field(min_length=1, max_length=MAX_ITEMS)
    dry_run: bool = True


class OrganizeFilesTool(BaseTool[OrganizeFilesArguments]):
    name = "organize_files"
    description = (
        "Plan and optionally apply a batch of file moves or renames inside the authorized "
        "project. Always returns a per-file plan plus the conflict list; dry_run=true only "
        "reports. Existing destinations are conflicts, never overwritten. Deletion is not "
        "part of this tool."
    )
    arguments_model = OrganizeFilesArguments
    risk = ToolRisk.R1
    has_side_effects = True
    parallel_safe = False

    def __init__(self, root: Path, *, authorization: ProjectAuthorization) -> None:
        self._root = root
        self._authorization = authorization

    async def invoke(self, arguments: OrganizeFilesArguments) -> str:
        if self._authorization is not ProjectAuthorization.READ_WRITE:
            raise ToolPermissionError(
                "项目当前是只读授权；整理文件需要先在页面上把该项目改为可写授权"
            )
        return await asyncio.to_thread(self._run, arguments)

    def _run(self, arguments: OrganizeFilesArguments) -> str:
        plan: MovePlan = plan_moves(
            self._root,
            [
                MoveRule(source=item.source, destination=item.destination)
                for item in arguments.rules
            ],
        )
        payload: dict[str, object] = {
            "dry_run": arguments.dry_run,
            "plan": plan.as_dict(),
        }
        if arguments.dry_run:
            payload["next_step"] = (
                "计划已生成，未修改任何文件。确认无误后用 dry_run=false 执行；"
                "存在冲突的条目不会被移动。"
            )
        else:
            result = apply_plan(self._root, plan)
            payload["result"] = result
            if result["failed"] is not None:
                payload["next_step"] = (
                    "执行中途失败，已完成的部分已按逆序回滚；请修正后重新生成计划。"
                )
            else:
                payload["next_step"] = "已按计划移动；每个文件的去向见 executed 列表。"
        return json.dumps(payload, ensure_ascii=False, indent=2)


def project_organize_tools(root: Path, *, authorization: ProjectAuthorization) -> list[BaseTool]:
    """只读授权下不注册：模型连"整理"这个概念都看不到。"""

    if authorization is not ProjectAuthorization.READ_WRITE:
        return []
    return [OrganizeFilesTool(root, authorization=authorization)]
