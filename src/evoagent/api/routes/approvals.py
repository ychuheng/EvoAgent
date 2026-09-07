"""工具审批查询与决策路由。"""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Request

from evoagent.api.schemas import ApprovalDecisionRequest, ApprovalResponse
from evoagent.tools.approvals import ApprovalService

router = APIRouter(prefix="/tool-approvals", tags=["approvals"])


def approval_service(request: Request) -> ApprovalService:
    return ApprovalService(request.app.state.database.session_factory)


ApprovalServiceDependency = Annotated[ApprovalService, Depends(approval_service)]


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
