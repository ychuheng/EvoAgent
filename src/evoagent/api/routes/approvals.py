"""工具审批查询与决策路由。"""

import asyncio
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request

from evoagent.api.schemas import ApprovalDecisionRequest, ApprovalResponse
from evoagent.db.models import ApprovalStatus, TaskRecord, ToolApprovalRecord, ToolCallRecord
from evoagent.projects.editing import EditRequest, LineRange, apply_edits
from evoagent.projects.service import load_task_project
from evoagent.tools.approvals import ApprovalService
from evoagent.tools.builtin.project_edit import ApplyPatchArguments, EditFileArguments

router = APIRouter(prefix="/tool-approvals", tags=["approvals"])


def approval_service(request: Request) -> ApprovalService:
    return ApprovalService(request.app.state.database.session_factory)


ApprovalServiceDependency = Annotated[ApprovalService, Depends(approval_service)]


@router.get("/{approval_id}/preview")
async def preview_approval(approval_id: UUID, request: Request) -> dict[str, Any]:
    """Recompute a pending file edit without writing, using its frozen tool arguments."""

    async with request.app.state.database.session_factory() as session:
        approval = await session.get(ToolApprovalRecord, approval_id)
        if approval is None:
            raise HTTPException(404, "审批不存在")
        if approval.status is not ApprovalStatus.PENDING:
            raise HTTPException(409, "审批已结束，请刷新任务")
        call = await session.get(ToolCallRecord, approval.tool_call_id)
        task = await session.get(TaskRecord, approval.task_id)
        if call is None or task is None or task.project_id is None:
            raise HTTPException(409, "审批没有可预览的项目编辑")
        if call.tool_name not in {"edit_file", "apply_patch"}:
            raise HTTPException(422, "此工具不支持文件差异预览")
        try:
            project = await load_task_project(session, task)
            if project is None or not project.writable:
                raise ValueError("项目写授权已失效")
            if call.tool_name == "edit_file":
                arguments = EditFileArguments.model_validate(call.arguments)
                edits = [arguments.to_request()]
            else:
                arguments = ApplyPatchArguments.model_validate(call.arguments)
                edits = [
                    EditRequest(
                        path=item.path,
                        replacement=item.replacement,
                        expected_sha256=item.expected_sha256,
                        old_text=item.old_text,
                        line_range=LineRange(item.start_line, item.end_line)
                        if item.start_line is not None and item.end_line is not None
                        else None,
                        create=item.create,
                    )
                    for item in arguments.edits
                ]
            outcome = await asyncio.to_thread(apply_edits, project.root, edits, dry_run=True)
        except Exception as error:
            raise HTTPException(409, f"无法生成当前差异，请重新读取项目：{error}") from error
        files = [
            {
                "path": item.display,
                "created": item.created,
                "added_lines": item.added_lines,
                "removed_lines": item.removed_lines,
                "diff": item.diff,
                "diff_truncated": item.diff_truncated,
            }
            for item in outcome.outcomes
        ]
        return {
            "approval_id": str(approval_id),
            "file_count": len(files),
            "added_lines": sum(item["added_lines"] for item in files),
            "removed_lines": sum(item["removed_lines"] for item in files),
            "files": files,
        }


@router.get("/{approval_id}", response_model=ApprovalResponse)
async def get_approval(
    approval_id: UUID,
    service: ApprovalServiceDependency,
) -> ApprovalResponse:
    record = await service.get(approval_id)
    return ApprovalResponse.model_validate(record, from_attributes=True)


@router.post("/{approval_id}/{decision}", response_model=ApprovalResponse)
async def decide_approval(
    approval_id: UUID,
    decision: Literal["approve", "reject"],
    request: ApprovalDecisionRequest,
    service: ApprovalServiceDependency,
) -> ApprovalResponse:
    record = await service.decide(
        approval_id,
        approved=decision == "approve",
        response=request.response,
    )
    return ApprovalResponse.model_validate(record, from_attributes=True)
