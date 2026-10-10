"""Trusted human trial controls; admission never bypasses independent evidence."""

from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from evoagent.api.dependencies import DatabaseDependency, SettingsDependency
from evoagent.db.models import SkillRecord, SkillVersionRecord
from evoagent.skills.trials import SkillTrialService, TrialError, TrialScope

router = APIRouter(tags=["skill-trials"])


class TrialDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_lock_version: int = Field(ge=0, strict=True)
    reason: str = Field(min_length=1, max_length=1000)


class TrialActivation(TrialDecision):
    workspace_id: UUID
    project_id: UUID | None = None
    validation_request_id: UUID


class TrialRollback(TrialDecision):
    target_trial_id: UUID


def trial_view(row):
    return {
        "id": row.id,
        "skill_id": row.skill_id,
        "version_id": row.version_id,
        "workspace_id": row.workspace_id,
        "project_id": row.project_id,
        "status": row.status,
        "lock_version": row.lock_version,
        "validation_request_id": row.validation_request_id,
        "report_hash": row.report_hash,
        "suspension_reason": row.suspension_reason,
    }


async def respond(operation):
    try:
        return await operation
    except TrialError as error:
        code = str(error)
        if code.startswith("trial_not_ready:"):
            raise HTTPException(
                422, detail={"code": "trial_not_ready", "reason": code.split(":", 1)[1]}
            ) from None
        status = 404 if code.endswith("missing") else 409 if "conflict" in code else 422
        raise HTTPException(status, detail={"code": code}) from None


def service(database, settings):
    return SkillTrialService(
        database.session_factory, trial_enabled=settings.personal_trial_enabled
    )


@router.get("/skill-versions/{version_id}/trial-readiness")
async def readiness(
    version_id: UUID,
    validation_request_id: UUID,
    database: DatabaseDependency,
    settings: SettingsDependency,
):
    result = await respond(service(database, settings).assess(version_id, validation_request_id))
    async with database.session_factory() as session:
        version = await session.get(SkillVersionRecord, version_id)
        skill = await session.get(SkillRecord, version.skill_id) if version else None
    return {
        "ready": settings.personal_trial_enabled and result.ready,
        "evidence_ready": result.ready,
        "reasons": result.reasons
        + (() if settings.personal_trial_enabled else ("trial_feature_not_ready",)),
        "report_hash": result.report_hash,
        "skill_lock_version": skill.lock_version if skill else None,
    }


@router.post("/skill-versions/{version_id}/trial", status_code=201)
async def activate(
    version_id: UUID,
    body: TrialActivation,
    database: DatabaseDependency,
    settings: SettingsDependency,
):
    result = await respond(
        service(database, settings).activate(
            version_id,
            TrialScope(body.workspace_id, body.project_id),
            body.validation_request_id,
            body.expected_lock_version,
            "local-user",
            body.reason,
        )
    )
    return trial_view(result)


@router.get("/skill-trials")
async def list_trials(
    workspace_id: UUID,
    database: DatabaseDependency,
    settings: SettingsDependency,
    project_id: UUID | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0, le=10000),
):
    rows = await respond(
        service(database, settings).list_for_scope(
            workspace_id, project_id, limit=limit, offset=offset
        )
    )
    return {"items": [trial_view(row) for row in rows]}


@router.post("/skill-trials/{trial_id}/suspend")
async def suspend(
    trial_id: UUID,
    body: TrialDecision,
    database: DatabaseDependency,
    settings: SettingsDependency,
):
    # The emergency brake remains usable even if new adoption is disabled.
    result = await respond(
        service(database, settings).suspend(trial_id, body.expected_lock_version, body.reason)
    )
    return trial_view(result)


@router.post("/skill-trials/{trial_id}/rollback")
async def rollback(
    trial_id: UUID,
    body: TrialRollback,
    database: DatabaseDependency,
    settings: SettingsDependency,
):
    result = await respond(
        service(database, settings).rollback(
            trial_id, body.target_trial_id, body.expected_lock_version, body.reason
        )
    )
    return trial_view(result)
