"""Skill 候选提炼的最小 HTTP 入口。"""

from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field

from evoagent.skills.extraction import CandidateGenerator, SkillExtractionService
from evoagent.skills.provenance import ProvenanceService
from evoagent.skills.validation import SkillDefinitionValidator
from evoagent.tools.registry import ToolRegistry
from evoagent.trace.artifacts import ArtifactService, LocalArtifactStore

router = APIRouter(prefix="/skills", tags=["skills"])


class SkillExtractionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_eval_run_ids: tuple[UUID, ...] = Field(min_length=1, max_length=100)


class SkillExtractionResponse(BaseModel):
    skill_id: UUID
    skill_version_id: UUID
    version: int
    content_hash: str
    created: bool
    lifecycle_status: str = "draft"


@router.post(
    "/extract", response_model=SkillExtractionResponse, status_code=status.HTTP_201_CREATED
)
async def extract_skill(
    payload: SkillExtractionRequest, request: Request
) -> SkillExtractionResponse:
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
    validator = SkillDefinitionValidator(
        registry,
        allowed_tools=frozenset(settings.skill_allowed_tools),
        max_steps=settings.skill_max_steps,
        max_risk=settings.skill_max_effective_risk,
        supported_schema_version=settings.skill_schema_version,
    )
    try:
        result = await SkillExtractionService(
            database.session_factory,
            provenance,
            generator,
            validator,
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
