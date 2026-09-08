"""FastAPI 应用工厂与进程生命周期。"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from evoagent.api.routes import approvals, evals, events, sessions, skills, tasks, traces
from evoagent.api.schemas import ErrorDetail, ErrorResponse, HealthResponse
from evoagent.config import ProviderName, Settings
from evoagent.db.repositories.base import RecordNotFoundError
from evoagent.db.session import Database
from evoagent.providers.openai_compatible import OpenAICompatibleProvider
from evoagent.skills.extraction import CandidateGenerator, ModelCandidateGenerator
from evoagent.skills.sanitizer import TraceSanitizer
from evoagent.skills.service import SkillServiceError
from evoagent.tasks.service import TaskServiceError
from evoagent.tools.approvals import ApprovalServiceError
from evoagent.tools.catalog import default_skill_tool_catalog
from evoagent.tools.registry import ToolRegistry
from evoagent.web.viewer import trace_viewer


def create_app(
    settings: Settings | None = None,
    *,
    database: Database | None = None,
    candidate_generator: CandidateGenerator | None = None,
    skill_tool_registry: ToolRegistry | None = None,
) -> FastAPI:
    """创建可在生产环境和测试中注入依赖的 API 应用。"""

    resolved_settings = settings or Settings()
    owns_database = database is None
    resolved_database = database or Database(resolved_settings.database_url.get_secret_value())
    resolved_skill_registry = skill_tool_registry or default_skill_tool_catalog()
    resolved_candidate_generator = candidate_generator
    owned_extractor_provider: OpenAICompatibleProvider | None = None
    if (
        resolved_candidate_generator is None
        and resolved_settings.skill_extractor_model is not None
        and resolved_settings.provider is ProviderName.OPENAI_COMPATIBLE
        and resolved_settings.api_key is not None
        and resolved_settings.base_url is not None
    ):
        owned_extractor_provider = OpenAICompatibleProvider(
            api_key=resolved_settings.api_key,
            base_url=str(resolved_settings.base_url),
            timeout_seconds=resolved_settings.model_timeout_seconds,
        )
        resolved_candidate_generator = ModelCandidateGenerator(
            owned_extractor_provider,
            model=resolved_settings.skill_extractor_model,
        )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.settings = resolved_settings
        app.state.database = resolved_database
        app.state.candidate_generator = resolved_candidate_generator
        app.state.skill_tool_registry = resolved_skill_registry
        app.state.trace_sanitizer = TraceSanitizer(resolved_settings.workspace)
        try:
            yield
        finally:
            if owned_extractor_provider is not None:
                await owned_extractor_provider.aclose()
            if owns_database:
                await resolved_database.dispose()

    app = FastAPI(title="EvoAgent API", version="0.3.0.dev0", lifespan=lifespan)
    app.include_router(sessions.router, prefix="/api/v1")
    app.include_router(tasks.router, prefix="/api/v1")
    app.include_router(approvals.router, prefix="/api/v1")
    app.include_router(events.router, prefix="/api/v1")
    app.include_router(traces.router, prefix="/api/v1")
    app.include_router(skills.router, prefix="/api/v1")
    app.include_router(skills.versions_router, prefix="/api/v1")
    app.include_router(evals.router, prefix="/api/v1")
    app.add_api_route("/viewer", trace_viewer, response_class=HTMLResponse, include_in_schema=False)
    if resolved_settings.frontend_dist.is_dir():
        app.mount(
            "/ui",
            StaticFiles(directory=resolved_settings.frontend_dist, html=True),
            name="skill-ui",
        )

    @app.exception_handler(TaskServiceError)
    async def handle_task_service_error(_request: Request, error: TaskServiceError) -> JSONResponse:
        response_status = (
            status.HTTP_404_NOT_FOUND
            if error.code.endswith("not_found")
            else status.HTTP_409_CONFLICT
        )
        body = ErrorResponse(error=ErrorDetail(code=error.code, message=str(error)))
        return JSONResponse(status_code=response_status, content=body.model_dump(mode="json"))

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        _request: Request, error: RequestValidationError
    ) -> JSONResponse:
        body = ErrorResponse(
            error=ErrorDetail(
                code="validation_error",
                message="request validation failed",
                details={
                    "errors": [
                        {
                            "type": item["type"],
                            "location": list(item["loc"]),
                            "message": item["msg"],
                        }
                        for item in error.errors()
                    ]
                },
            )
        )
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content=body.model_dump(mode="json"),
        )

    @app.exception_handler(ApprovalServiceError)
    async def handle_approval_error(_request: Request, error: ApprovalServiceError) -> JSONResponse:
        body = ErrorResponse(error=ErrorDetail(code=error.code, message=str(error)))
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content=body.model_dump(mode="json"),
        )

    @app.exception_handler(RecordNotFoundError)
    async def handle_record_not_found(
        _request: Request, error: RecordNotFoundError
    ) -> JSONResponse:
        body = ErrorResponse(error=ErrorDetail(code="record_not_found", message=str(error)))
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content=body.model_dump(mode="json"),
        )

    @app.exception_handler(SkillServiceError)
    async def handle_skill_service_error(
        _request: Request, error: SkillServiceError
    ) -> JSONResponse:
        body = ErrorResponse(error=ErrorDetail(code=error.code, message=str(error)))
        response_status = (
            status.HTTP_409_CONFLICT
            if error.code in {"version_conflict", "invalid_skill_transition"}
            else status.HTTP_422_UNPROCESSABLE_CONTENT
        )
        return JSONResponse(
            status_code=response_status,
            content=body.model_dump(mode="json"),
        )

    @app.get("/health/live", response_model=HealthResponse, tags=["health"])
    async def liveness() -> HealthResponse:
        return HealthResponse(status="ok")

    @app.get(
        "/health/ready",
        response_model=HealthResponse,
        responses={503: {"model": ErrorResponse}},
        tags=["health"],
    )
    async def readiness(request: Request) -> HealthResponse | JSONResponse:
        try:
            async with request.app.state.database.engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
        except Exception:
            body = ErrorResponse(
                error=ErrorDetail(
                    code="database_unavailable",
                    message="database readiness check failed",
                )
            )
            return JSONResponse(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                content=body.model_dump(mode="json"),
            )
        return HealthResponse(status="ok")

    return app
