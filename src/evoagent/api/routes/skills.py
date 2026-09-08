"""Skill 提炼、查询、评审、发布与回滚 HTTP 入口。"""

from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field, field_validator

from evoagent.skills.extraction import CandidateGenerator, SkillExtractionService
from evoagent.skills.lifecycle import SkillStatus
from evoagent.skills.provenance import ProvenanceService
from evoagent.skills.schema import SkillDefinition
from evoagent.skills.service import PromotionResult, SkillService
from evoagent.skills.validation import SkillDefinitionValidator
from evoagent.tools.registry import ToolRegistry
from evoagent.trace.artifacts import ArtifactService, LocalArtifactStore

router = APIRouter(prefix="/skills", tags=["skills"])
versions_router = APIRouter(prefix="/skill-versions", tags=["skill-versions"])


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SkillExtractionRequest(StrictModel):
    source_eval_run_ids: tuple[UUID, ...] = Field(min_length=1, max_length=100)


class SkillExtractionResponse(StrictModel):
    skill_id: UUID
    skill_version_id: UUID
    version: int
    content_hash: str
    created: bool
    lifecycle_status: str = "draft"


class SkillVersionCreateRequest(StrictModel):
    definition: SkillDefinition
    parent_version_id: UUID | None = None


class ReviewRequest(StrictModel):
    action: Literal["approve", "reject"]
    expected_lock_version: int = Field(ge=0)
    reviewer: str = Field(min_length=1, max_length=128)
    reason: str = Field(min_length=1, max_length=4_000)

    @field_validator("reviewer", "reason")
    @classmethod
    def strip_text(cls, value: str) -> str:
        return value.strip()


class StatusRequest(StrictModel):
    expected_lock_version: int = Field(ge=0)
    reviewer: str = Field(min_length=1, max_length=128)
    reason: str = Field(min_length=1, max_length=4_000)


class RollbackRequest(StatusRequest):
    target_version_id: UUID


class PromotionResponse(StrictModel):
    skill_id: UUID
    active_version_id: UUID | None
    status: SkillStatus
    lock_version: int


def _registry(request: Request) -> ToolRegistry:
    return request.app.state.skill_tool_registry or ToolRegistry()


def _validator(request: Request) -> SkillDefinitionValidator:
    settings = request.app.state.settings
    return SkillDefinitionValidator(
        _registry(request),
        allowed_tools=frozenset(settings.skill_allowed_tools),
        max_steps=settings.skill_max_steps,
        max_risk=settings.skill_max_effective_risk,
        supported_schema_version=settings.skill_schema_version,
    )


def _service(request: Request) -> SkillService:
    return SkillService(request.app.state.database.session_factory, _validator(request))


def _promotion(result: PromotionResult) -> PromotionResponse:
    return PromotionResponse(
        skill_id=result.skill_id,
        active_version_id=result.active_version_id,
        status=result.status,
        lock_version=result.lock_version,
    )


async def _extract(payload: SkillExtractionRequest, request: Request) -> SkillExtractionResponse:
    generator: CandidateGenerator | None = request.app.state.candidate_generator
    registry: ToolRegistry | None = request.app.state.skill_tool_registry
    if generator is None or registry is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "skill extraction is not configured"
        )
    settings = request.app.state.settings
    database = request.app.state.database
    artifacts = ArtifactService(
        LocalArtifactStore(settings.artifact_root), database.session_factory
    )
    provenance = ProvenanceService(
        database.session_factory,
        artifacts,
        request.app.state.trace_sanitizer,
        allowed_tools=frozenset(settings.skill_allowed_tools),
        max_risk=settings.skill_max_effective_risk,
    )
    try:
        result = await SkillExtractionService(
            database.session_factory,
            provenance,
            generator,
            _validator(request),
            max_sources=settings.skill_max_sources,
        ).extract(payload.source_eval_run_ids)
    except ValueError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from error
    return SkillExtractionResponse(
        skill_id=result.skill_id,
        skill_version_id=result.skill_version_id,
        version=result.version,
        content_hash=result.content_hash,
        created=result.created,
    )


@router.post(
    "/extractions",
    response_model=SkillExtractionResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def extract_skill(
    payload: SkillExtractionRequest, request: Request
) -> SkillExtractionResponse:
    return await _extract(payload, request)


@router.post(
    "/extract",
    response_model=SkillExtractionResponse,
    status_code=status.HTTP_201_CREATED,
    include_in_schema=False,
)
async def extract_skill_compat(
    payload: SkillExtractionRequest, request: Request
) -> SkillExtractionResponse:
    """兼容模块 5 已公开的旧路径。"""

    return await _extract(payload, request)


@router.get("")
async def list_skills(request: Request) -> tuple[dict[str, Any], ...]:
    return await _service(request).list_skills()


@router.get("/{skill_id}")
async def get_skill(skill_id: UUID, request: Request) -> dict[str, Any]:
    return await _service(request).get_skill(skill_id)


@router.post("/{skill_id}/versions", status_code=status.HTTP_201_CREATED)
async def create_version(
    skill_id: UUID, payload: SkillVersionCreateRequest, request: Request
) -> dict[str, Any]:
    try:
        version = await _service(request).create_version(
            skill_id=skill_id,
            definition=payload.definition,
            parent_version_id=payload.parent_version_id,
        )
    except ValueError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from error
    return await _service(request).get_version(version.id)


@router.post("/{skill_id}/rollback", response_model=PromotionResponse)
async def rollback_skill(
    skill_id: UUID, payload: RollbackRequest, request: Request
) -> PromotionResponse:
    return _promotion(
        await _service(request).rollback(
            skill_id=skill_id,
            target_version_id=payload.target_version_id,
            expected_lock_version=payload.expected_lock_version,
            reviewer=payload.reviewer,
            reason=payload.reason,
        )
    )


@router.post("/{skill_id}/{operation}", response_model=PromotionResponse)
async def change_skill_status(
    skill_id: UUID,
    operation: Literal["disable", "enable", "deprecate"],
    payload: StatusRequest,
    request: Request,
) -> PromotionResponse:
    targets = {
        "disable": SkillStatus.DISABLED,
        "enable": SkillStatus.ENABLED,
        "deprecate": SkillStatus.DEPRECATED,
    }
    return _promotion(
        await _service(request).set_status(
            skill_id=skill_id,
            target=targets[operation],
            expected_lock_version=payload.expected_lock_version,
            reviewer=payload.reviewer,
            reason=payload.reason,
        )
    )


@versions_router.get("/{version_id}")
async def get_version(version_id: UUID, request: Request) -> dict[str, Any]:
    return await _service(request).get_version(version_id)


@versions_router.get("/{version_id}/diff")
async def diff_version(
    version_id: UUID,
    request: Request,
    against: Annotated[UUID, Query()],
) -> dict[str, Any]:
    try:
        changes = await _service(request).diff_versions(version_id, against)
    except ValueError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from error
    return {"version_id": str(version_id), "against": str(against), "changes": changes}


@versions_router.post("/{version_id}/review", response_model=PromotionResponse)
async def review_version(
    version_id: UUID, payload: ReviewRequest, request: Request
) -> PromotionResponse:
    service = _service(request)
    handler = service.approve if payload.action == "approve" else service.reject
    result = await handler(
        version_id=version_id,
        expected_lock_version=payload.expected_lock_version,
        reviewer=payload.reviewer,
        reason=payload.reason,
    )
    return _promotion(result)
