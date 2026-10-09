"""FastAPI 应用工厂与进程生命周期。"""

import mimetypes
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from evoagent.api.routes import (
    approvals,
    artifacts,
    evals,
    events,
    memory,
    projects,
    sessions,
    skills,
    tasks,
    traces,
)
from evoagent.api.schemas import ErrorDetail, ErrorResponse, HealthResponse, RuntimeInfoResponse
from evoagent.config import ProviderName, Settings
from evoagent.db.repositories.base import RecordNotFoundError
from evoagent.db.session import Database
from evoagent.mcp.connections import ConnectionManager
from evoagent.mcp.schema import MCPError
from evoagent.mcp.service import MCPService
from evoagent.memory.extraction import ModelMemoryExtractor
from evoagent.memory.schema import MemoryError
from evoagent.providers.openai_compatible import OpenAICompatibleProvider
from evoagent.runtime.budget import BudgetScope
from evoagent.skills.extraction import CandidateGenerator, ModelCandidateGenerator
from evoagent.skills.sanitizer import TraceSanitizer
from evoagent.skills.service import SkillServiceError
from evoagent.tasks.service import TaskServiceError
from evoagent.tools.approvals import ApprovalServiceError
from evoagent.tools.catalog import default_skill_tool_catalog
from evoagent.tools.registry import ToolRegistry
from evoagent.web.viewer import trace_viewer
from evoagent.workers.rate_limit import BudgetedProvider


def create_app(
    settings: Settings | None = None,
    *,
    database: Database | None = None,
    candidate_generator: CandidateGenerator | None = None,
    skill_tool_registry: ToolRegistry | None = None,
    memory_generator=None,
) -> FastAPI:
    """创建可在生产环境和测试中注入依赖的 API 应用。"""

    resolved_settings = settings or Settings()
    owns_database = database is None
    resolved_database = database or Database(resolved_settings.database_url.get_secret_value())
    resolved_skill_registry = skill_tool_registry or default_skill_tool_catalog()
    resolved_candidate_generator = candidate_generator
    owned_extractor_provider: OpenAICompatibleProvider | None = None
    owned_memory_provider = None
    if memory_generator is None and resolved_settings.memory_extractor_model:
        if (
            resolved_settings.provider is not ProviderName.OPENAI_COMPATIBLE
            or resolved_settings.api_key is None
            or resolved_settings.base_url is None
        ):
            raise ValueError("memory extractor requires configured real provider")
        owned_memory_provider = OpenAICompatibleProvider(
            api_key=resolved_settings.api_key,
            base_url=str(resolved_settings.base_url),
            timeout_seconds=10,
        )
        memory_generator = ModelMemoryExtractor(
            BudgetedProvider(
                owned_memory_provider,
                settings=resolved_settings,
                session_factory=resolved_database.session_factory,
                scope=BudgetScope(resolved_settings.budget_scope),
                task_id=None,
                run_id=None,
                provider_name=resolved_settings.provider.value,
                model=resolved_settings.memory_extractor_model,
            ),
            resolved_settings.memory_extractor_model,
        )
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
            BudgetedProvider(
                owned_extractor_provider,
                settings=resolved_settings,
                session_factory=resolved_database.session_factory,
                scope=BudgetScope(resolved_settings.budget_scope),
                task_id=None,
                run_id=None,
                provider_name=resolved_settings.provider.value,
                model=resolved_settings.skill_extractor_model,
            ),
            model=resolved_settings.skill_extractor_model,
            max_output_tokens=min(resolved_settings.model_request_max_output_tokens or 4096, 4096),
        )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        from evoagent.workers.presence import WorkerPresence
        from evoagent.workers.wakeup import Wakeup, redis_client

        client = redis_client(resolved_settings)
        app.state.worker_presence = WorkerPresence(client, resolved_settings.redis_namespace)
        from evoagent.trace.notifications import RunEventNotifier

        notifier = (
            RunEventNotifier(client, resolved_settings.redis_namespace)
            if resolved_settings.runtime_shared_notifications_enabled
            else None
        )
        if notifier is not None:
            await notifier.start()
        app.state.event_notifier = notifier
        resolved_database.session_factory.configure(
            info={
                "wakeup": Wakeup(client, resolved_settings.redis_namespace),
                **({"event_notifier": notifier} if notifier is not None else {}),
            }
        )
        app.state.settings = resolved_settings
        app.state.memory_generator = memory_generator
        app.state.database = resolved_database
        app.state.candidate_generator = resolved_candidate_generator
        app.state.skill_tool_registry = resolved_skill_registry
        app.state.trace_sanitizer = TraceSanitizer(resolved_settings.workspace)
        app.state.mcp_manager = ConnectionManager(resolved_settings)
        app.state.mcp_service = MCPService(
            resolved_database.session_factory, resolved_settings, app.state.mcp_manager
        )
        try:
            yield
        finally:
            resolved_database.session_factory.configure(info={})
            if notifier is not None:
                await notifier.close()
            if client is not None:
                await client.aclose()
            await app.state.mcp_manager.aclose()
            if owned_memory_provider is not None:
                await owned_memory_provider.aclose()
            if owned_extractor_provider is not None:
                await owned_extractor_provider.aclose()
            if owns_database:
                await resolved_database.dispose()

    app = FastAPI(title="EvoAgent API", version="0.4.0.dev0", lifespan=lifespan)
    app.include_router(memory.router, prefix="/api/v1")
    from evoagent.api.routes import mcp, retrieval, runtime_evals

    app.include_router(mcp.router, prefix="/api/v1")
    app.include_router(runtime_evals.router, prefix="/api/v1")
    app.include_router(retrieval.router, prefix="/api/v1")
    app.include_router(sessions.router, prefix="/api/v1")
    app.include_router(sessions.workspaces_router, prefix="/api/v1")
    app.include_router(projects.router, prefix="/api/v1")
    app.include_router(artifacts.router, prefix="/api/v1")
    app.include_router(tasks.router, prefix="/api/v1")
    app.include_router(approvals.router, prefix="/api/v1")
    app.include_router(events.router, prefix="/api/v1")
    app.include_router(traces.router, prefix="/api/v1")
    app.include_router(skills.router, prefix="/api/v1")
    app.include_router(skills.versions_router, prefix="/api/v1")
    app.include_router(evals.router, prefix="/api/v1")
    app.add_api_route("/viewer", trace_viewer, response_class=HTMLResponse, include_in_schema=False)
    if resolved_settings.frontend_dist.is_dir():
        # Windows may register .js as text/plain. Browsers refuse to execute an
        # ES module served with that MIME type, leaving the UI entirely blank.
        mimetypes.add_type("text/javascript", ".js")
        app.mount(
            "/ui",
            StaticFiles(directory=resolved_settings.frontend_dist, html=True),
            name="skill-ui",
        )

    @app.exception_handler(MemoryError)
    async def handle_memory_error(_request: Request, error: MemoryError) -> JSONResponse:
        return JSONResponse(
            status_code=404 if error.code.endswith("not_found") else 409,
            content={"error": {"code": error.code, "message": str(error)}},
        )

    @app.exception_handler(MCPError)
    async def handle_mcp_error(_request: Request, error: MCPError) -> JSONResponse:
        return JSONResponse(
            status_code=404 if error.code.endswith("not_found") else 409,
            content={"error": {"code": error.code, "message": error.code}},
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

    @app.get("/api/v1/runtime-info", response_model=RuntimeInfoResponse, tags=["runtime"])
    async def runtime_info() -> RuntimeInfoResponse:
        worker_ready = await app.state.worker_presence.is_ready()
        return RuntimeInfoResponse(
            provider_mode=("mock" if resolved_settings.provider is ProviderName.MOCK else "real"),
            provider=resolved_settings.provider.value,
            model=resolved_settings.model or "mock-model",
            search_mode=resolved_settings.search_provider,
            memory_enabled=resolved_settings.memory_retrieval_enabled,
            code_version=resolved_settings.code_version,
            execution_mode=(
                "trusted_windows_host" if resolved_settings.trusted_host_mode else "container"
            ),
            worker_status=(
                "unknown" if worker_ready is None else "ready" if worker_ready else "missing"
            ),
        )

    @app.get("/api/v1/budget-status", tags=["runtime"])
    async def budget_status() -> dict:
        """M0c 闸门状态：额度、价格假设与停止阈值是否已填、当前花费多少。

        未填数值时正式评测不会启动；页面与预检据此提示"还差什么"，而不是等到付费调用才失败。
        """

        from evoagent.runtime.budget import BudgetScope, evaluate_budget, limits_from_settings

        scope = (
            BudgetScope.TRIAL
            if resolved_settings.budget_scope == BudgetScope.TRIAL.value
            else BudgetScope.FORMAL
        )
        limits = limits_from_settings(resolved_settings, scope)
        async with resolved_database.session_factory() as session:
            status = await evaluate_budget(session, resolved_settings, scope=scope)
        return {
            "scope": scope.value,
            "allowed": status.allowed,
            "reason": status.reason,
            "limit_micros": status.limit_micros,
            "spent_micros": status.spent_micros,
            "stop_threshold_micros": status.stop_threshold_micros,
            "task_limit_micros": status.task_limit_micros,
            "priced": limits.priced,
            "trial_limit_configured": resolved_settings.budget_trial_limit_micros is not None,
            "total_limit_configured": resolved_settings.budget_total_limit_micros is not None,
            "note": (
                "本接口只报告闸门状态，不返回任何密钥。未填额度或价格假设时，付费模型调用会被拒绝。"
            ),
        }

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
