"""Trusted local control plane; models cannot grant learning consent or activate skills."""

from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Response

from evoagent.api.dependencies import DatabaseDependency, SettingsDependency
from evoagent.db.repositories.base import ConcurrentUpdateError
from evoagent.evals.validators import default_validator_registry
from evoagent.learning.configuration import candidate_configuration
from evoagent.learning.judgments import HumanJudgmentSubmission, ValidationJudgmentService
from evoagent.learning.schema import (
    CandidateReview,
    FeedbackPayload,
    FeedbackSubmission,
    FeedbackView,
    LearningDecision,
    LearningError,
    LearningPolicySubmission,
    LearningRequestView,
    LearningRetry,
    LearningSubmission,
    SourceRevocation,
)
from evoagent.learning.service import LearningService
from evoagent.learning.sources import PersonalSourceService
from evoagent.learning.validation import PersonalValidationService, ValidationAdmission
from evoagent.learning.validation_schema import ValidationStart
from evoagent.trace.artifacts import LocalArtifactStore

router = APIRouter(tags=["learning"])


def service(database, settings):
    return LearningService(
        database.session_factory,
        learning_enabled=settings.learning_enabled,
        generator_configuration=candidate_configuration(settings),
    )


async def respond(operation):
    try:
        return await operation
    except ConcurrentUpdateError as error:
        raise HTTPException(409, detail={"code": str(error).split(":", 1)[0]}) from error
    except LearningError as error:
        status = (
            404
            if error.code.endswith("not_found")
            else 409
            if error.code
            in {
                "learning_disabled",
                "learning_policy_off",
                "learning_usage_unresolved",
            }
            else 422
        )
        raise HTTPException(status, detail={"code": error.code}) from error
    except ValueError as error:
        # Repository errors never cause reflection of user-submitted payloads.
        raise HTTPException(422, detail={"code": "invalid_learning_payload"}) from error


@router.post("/runs/{run_id}/feedback", status_code=201, response_model=FeedbackView)
async def record_feedback(
    run_id: UUID,
    body: FeedbackSubmission,
    database: DatabaseDependency,
    settings: SettingsDependency,
):
    payload = FeedbackPayload.model_validate(
        body.model_dump(exclude={"client_request_id", "expected_revision"})
    )
    return await respond(
        service(database, settings).record_feedback(
            run_id,
            payload,
            client_request_id=body.client_request_id,
            expected_revision=body.expected_revision,
        )
    )


@router.get("/runs/{run_id}/skill-observation-evidence")
async def skill_observation_evidence(run_id: UUID, database: DatabaseDependency):
    from evoagent.skills.observation_catalog import observation_catalog

    return await respond(observation_catalog(database.session_factory, run_id))


@router.post(
    "/runs/{run_id}/learning-requests", status_code=202, response_model=LearningRequestView
)
async def request_learning(
    run_id: UUID,
    body: LearningSubmission,
    response: Response,
    database: DatabaseDependency,
    settings: SettingsDependency,
):
    result = await respond(service(database, settings).request_learning(run_id, body))
    response.headers["Location"] = f"/api/v1/learning-requests/{result.id}"
    return result


@router.get("/learning-requests/{request_id}", response_model=LearningRequestView)
async def get_request(request_id: UUID, database: DatabaseDependency, settings: SettingsDependency):
    return await respond(service(database, settings).get_request(request_id))


@router.get("/learning-requests")
async def list_requests(
    workspace_id: UUID,
    database: DatabaseDependency,
    settings: SettingsDependency,
    cursor: UUID | None = None,
    status: str | None = None,
    limit: int = Query(default=50, ge=1, le=100),
):
    return await respond(
        service(database, settings).list_requests(
            workspace_id, cursor=cursor, status=status, limit=limit
        )
    )


@router.get("/workspaces/{workspace_id}/learning-policy")
async def get_policy(
    workspace_id: UUID, database: DatabaseDependency, settings: SettingsDependency
):
    result = await respond(service(database, settings).policy(workspace_id))
    from evoagent.learning.validation_profiles import real_profile

    try:
        real_profile(settings)
        real_available = bool(
            settings.learning_enabled
            and result["mode"] != "off"
            and all(
                result[key] is not None and result[key] > 0
                for key in ("daily_limit_micros", "request_limit_micros")
            )
        )
    except LearningError:
        real_available = False
    return {
        **result,
        "learning_enabled": settings.learning_enabled,
        "automatic_discovery_available": False,
        "personal_validation_available": settings.learning_enabled,
        "personal_validation_provider": "mock",
        "personal_real_validation_available": real_available,
        "trial_adoption_available": real_available and settings.personal_trial_enabled,
    }


@router.put("/workspaces/{workspace_id}/learning-policy")
async def update_policy(
    workspace_id: UUID,
    body: LearningPolicySubmission,
    database: DatabaseDependency,
    settings: SettingsDependency,
):
    return await respond(service(database, settings).update_policy(workspace_id, body))


@router.post("/learning-requests/{request_id}/cancel", response_model=LearningRequestView)
async def cancel_request(
    request_id: UUID,
    body: LearningDecision,
    database: DatabaseDependency,
    settings: SettingsDependency,
):
    return await respond(
        service(database, settings).cancel_request(request_id, body.expected_lock_version)
    )


@router.post("/learning-requests/{request_id}/review", response_model=LearningRequestView)
async def review_candidate(
    request_id: UUID,
    body: CandidateReview,
    database: DatabaseDependency,
    settings: SettingsDependency,
):
    return await respond(
        service(database, settings).review_candidate(
            request_id, body.action, body.expected_lock_version, body.reason
        )
    )


def validation_service(database, settings):
    from evoagent.learning.replicas import default_personal_fixtures

    return PersonalValidationService(
        database.session_factory,
        LocalArtifactStore(settings.artifact_root),
        default_validator_registry(),
        learning_enabled=settings.learning_enabled,
        fixtures=default_personal_fixtures(),
        settings=settings,
    )


@router.get("/personal-validation-fixtures")
async def validation_fixture_catalog():
    from evoagent.learning.replicas import default_personal_fixtures

    return [
        {
            "fixture_id": fixture.fixture_id,
            "manifest": fixture.manifest(),
            "input_fingerprint": fixture.input_fingerprint(),
            "files": [
                {"path": path, "content": data.decode("utf8")} for path, data in fixture.files
            ],
        }
        for fixture in default_personal_fixtures()
    ]


@router.get("/personal-validation-profiles")
async def validation_profiles(settings: SettingsDependency):
    from evoagent.learning.validation_profiles import real_profile

    result = [{"profile_id": "offline-mock-v1", "available": True, "provider": "mock"}]
    try:
        profile = real_profile(settings)
        result.append(
            {
                "profile_id": profile.profile_id,
                "available": True,
                "provider": profile.provider,
                "model": profile.model,
                "profile_hash": profile.identity()["profile_hash"],
                "max_output_tokens": profile.max_output_tokens,
                "max_iterations": profile.max_iterations,
            }
        )
    except LearningError as error:
        result.append({"profile_id": "host-real-v1", "available": False, "reason": error.code})
    return result


@router.post(
    "/learning-requests/{request_id}/validations",
    status_code=202,
    response_model=LearningRequestView,
)
async def prepare_validation(
    request_id: UUID,
    body: ValidationAdmission,
    response: Response,
    database: DatabaseDependency,
    settings: SettingsDependency,
):
    result = await respond(validation_service(database, settings).prepare_cases(request_id, body))
    response.headers["Location"] = f"/api/v1/learning-requests/{result.id}"
    return result


@router.post(
    "/learning-requests/{request_id}/validation-start",
    status_code=202,
    response_model=LearningRequestView,
)
async def start_validation(
    request_id: UUID,
    body: ValidationStart,
    database: DatabaseDependency,
    settings: SettingsDependency,
):
    return await respond(
        validation_service(database, settings).start(request_id, body.expected_lock_version)
    )


@router.post("/learning-requests/{request_id}/judgments", status_code=201)
async def judge_validation(
    request_id: UUID,
    body: HumanJudgmentSubmission,
    database: DatabaseDependency,
    settings: SettingsDependency,
):
    return await respond(
        ValidationJudgmentService(
            database.session_factory,
            LocalArtifactStore(settings.artifact_root),
            learning_enabled=settings.learning_enabled,
        ).submit(request_id, body)
    )


@router.post("/learning-requests/{request_id}/reject", response_model=LearningRequestView)
async def reject_request(
    request_id: UUID,
    body: LearningDecision,
    database: DatabaseDependency,
    settings: SettingsDependency,
):
    return await respond(
        service(database, settings).reject_request(
            request_id, body.expected_lock_version, body.reason
        )
    )


@router.post(
    "/learning-requests/{request_id}/retry", status_code=202, response_model=LearningRequestView
)
async def retry_request(
    request_id: UUID,
    body: LearningRetry,
    database: DatabaseDependency,
    settings: SettingsDependency,
):
    return await respond(
        service(database, settings).retry_request(
            request_id, body.expected_lock_version, body.client_request_id
        )
    )


@router.post("/learning-sources/{source_id}/revoke")
async def revoke_source(source_id: UUID, body: SourceRevocation, database: DatabaseDependency):
    from sqlalchemy import select

    from evoagent.db.models import LearningRequestRecord, SkillSourceRecord

    source = await respond(
        PersonalSourceService(database.session_factory).revoke(
            source_id,
            body.reason,
            expected_status=body.expected_status,
        )
    )
    async with database.session_factory() as session:
        requests = list(
            await session.scalars(
                select(LearningRequestRecord.id)
                .where(
                    LearningRequestRecord.origin_run_id == source.run_id,
                    LearningRequestRecord.frozen_inputs["source_revision"].as_string()
                    == source.source_revision,
                )
                .order_by(LearningRequestRecord.id)
                .limit(101)
            )
        )
        versions = list(
            await session.scalars(
                select(SkillSourceRecord.skill_version_id)
                .where(
                    SkillSourceRecord.learning_source_id == source_id,
                )
                .order_by(SkillSourceRecord.skill_version_id)
                .limit(101)
            )
        )
    return {
        "source": source,
        "affected_request_ids": requests[:100],
        "affected_version_ids": versions[:100],
        "impact_truncated": len(requests) > 100 or len(versions) > 100,
    }
