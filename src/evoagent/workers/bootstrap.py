"""根据配置组装并启动独立 Worker 进程。"""

import asyncio
import signal
from collections.abc import AsyncIterator
from contextlib import suppress
from uuid import UUID

from evoagent.config import ProviderName, Settings
from evoagent.core.context import ContextBuilder
from evoagent.core.models import (
    FinishReason,
    Message,
    MessageRole,
    ModelRequest,
    ModelResponse,
    ProviderEvent,
    ToolCall,
)
from evoagent.db.models import RunRecord
from evoagent.db.session import Database
from evoagent.memory.maintenance import MaintenanceWorker
from evoagent.providers.base import ModelProvider
from evoagent.providers.mock import MockProvider
from evoagent.providers.openai_compatible import OpenAICompatibleProvider
from evoagent.retrieval.embeddings import provider_from_settings
from evoagent.retrieval.indexing import IndexService
from evoagent.runtime.persistent_runner import PersistentAgentRunner
from evoagent.sandbox.client import ControllerExecutor, DisabledSandboxExecutor
from evoagent.tasks.lease import JobLease, JobLeaseManager, TaskExecutionResult
from evoagent.tasks.lease_guard import LeaseGuard
from evoagent.tasks.state_machine import PersistentRunStatus
from evoagent.tools.builtin.artifact_read import ArtifactReadTool
from evoagent.tools.builtin.artifact_write import ArtifactWriteTool
from evoagent.tools.builtin.ask_user import AskUserTool
from evoagent.tools.builtin.calculator import CalculatorTool
from evoagent.tools.builtin.file_read import FileReadTool
from evoagent.tools.builtin.file_write import FileWriteTool
from evoagent.tools.builtin.shell import ShellTool
from evoagent.tools.builtin.web_fetch import WebFetchTool
from evoagent.tools.builtin.web_search import (
    BraveSearchProvider,
    MockSearchProvider,
    SearchResult,
    WebSearchTool,
)
from evoagent.tools.guards import URLGuard
from evoagent.tools.output_store import ToolOutputStore
from evoagent.tools.registry import ToolRegistry
from evoagent.tools.sandbox import RunSandbox
from evoagent.trace.artifacts import ArtifactService, LocalArtifactStore
from evoagent.workers.main import JobWorker
from evoagent.workers.rate_limit import GatedProvider, ServiceGate
from evoagent.workers.wakeup import Wakeup, redis_client


class WorkerDemoProvider:
    """根据已持久化消息推进搜索、写报告和最终回答的离线 Provider。"""

    def __init__(self, run_id: UUID) -> None:
        self._run_id = run_id

    async def stream(self, request: ModelRequest) -> AsyncIterator[ProviderEvent]:
        called_tools = {
            call.name
            for message in request.messages
            for call in message.tool_calls
            if message.role is MessageRole.ASSISTANT
        }
        if "file_write" in called_tools:
            response = ModelResponse(
                message=Message(
                    role=MessageRole.ASSISTANT,
                    content="离线调研完成，报告已保存为 report.md。",
                ),
                finish_reason=FinishReason.STOP,
            )
        elif "web_search" in called_tools:
            response = ModelResponse(
                message=Message(
                    role=MessageRole.ASSISTANT,
                    tool_calls=(
                        ToolCall(
                            call_id=f"demo-write-{self._run_id}",
                            name="file_write",
                            arguments={
                                "path": "report.md",
                                "content": (
                                    "# EvoAgent 离线调研报告\n\n"
                                    "MockSearchProvider 返回了确定性资料；"
                                    "本文件用于验证搜索、写入、副作用账本与恢复链路。\n"
                                ),
                            },
                        ),
                    ),
                ),
                finish_reason=FinishReason.TOOL_CALLS,
            )
        else:
            response = ModelResponse(
                message=Message(
                    role=MessageRole.ASSISTANT,
                    tool_calls=(
                        ToolCall(
                            call_id=f"demo-search-{self._run_id}",
                            name="web_search",
                            arguments={"query": "EvoAgent 可靠任务执行", "count": 1},
                        ),
                    ),
                ),
                finish_reason=FinishReason.TOOL_CALLS,
            )
        async for event in MockProvider([response]).stream(request):
            yield event


class ConfiguredTaskHandler:
    """为每个 Run 创建独立 Provider、工具和持久化 Runner。"""

    def __init__(self, settings: Settings, database: Database, gate=None) -> None:
        self._settings = settings
        self._database = database
        self._gate = gate or ServiceGate(settings)

    async def handle(self, lease: JobLease) -> TaskExecutionResult:
        from evoagent.evals.runtime import settings_for_run

        settings = await settings_for_run(
            self._database.session_factory, lease.run_id, self._settings
        )
        handler = ConfiguredTaskHandler(settings, self._database, self._gate)
        return await handler._handle(lease)

    async def _handle(self, lease: JobLease) -> TaskExecutionResult:
        async with self._database.session_factory() as session:
            run = await session.get(RunRecord, lease.run_id)
        expected_model = self._settings.model or "mock-model"
        if (
            run is None
            or run.provider != self._settings.provider.value
            or run.model != expected_model
        ):
            return TaskExecutionResult(
                status=PersistentRunStatus.FAILED,
                error_code="provider_configuration_mismatch",
                error_message="worker model configuration differs from the queued run",
            )
        provider = self._provider(lease.run_id)

        async def check():
            async with self._database.session_factory() as session:
                await LeaseGuard(lease).check(session)

        gated = GatedProvider(provider, self._gate, f"model:{self._settings.model}", check)
        search_provider = self._search_provider()
        web_fetch = WebFetchTool(URLGuard(), timeout_seconds=self._settings.tool_timeout_seconds)
        artifact_service = ArtifactService(
            LocalArtifactStore(self._settings.artifact_root),
            self._database.session_factory,
            lease_guard=LeaseGuard(lease),
        )
        registry = ToolRegistry(
            [
                CalculatorTool(),
                FileReadTool(self._settings.workspace),
                FileWriteTool(RunSandbox(self._settings.artifact_root, lease.run_id)),
                ArtifactWriteTool(lease.run_id, artifact_service),
                ArtifactReadTool(
                    ToolOutputStore(lease.run_id, artifact_service, self._database.session_factory)
                ),
                WebSearchTool(search_provider),
                web_fetch,
                AskUserTool(),
                ShellTool(
                    ControllerExecutor(self._settings, lease)
                    if self._settings.shell_sandbox_profile
                    else DisabledSandboxExecutor()
                ),
            ]
        )
        runner = PersistentAgentRunner(
            settings=self._settings,
            session_factory=self._database.session_factory,
            context_builder=ContextBuilder(),
            provider=gated,
            registry=registry,
            service_gate=self._gate,
        )
        try:
            return await runner.handle(lease)
        finally:
            await web_fetch.aclose()
            close_search = getattr(search_provider, "aclose", None)
            if close_search is not None:
                await close_search()
            if isinstance(provider, OpenAICompatibleProvider):
                await provider.aclose()

    def _provider(self, run_id: UUID) -> ModelProvider:
        if self._settings.provider is ProviderName.MOCK:
            return WorkerDemoProvider(run_id)
        if self._settings.api_key is None or self._settings.base_url is None:
            raise RuntimeError("real provider configuration was not validated")
        return OpenAICompatibleProvider(
            api_key=self._settings.api_key,
            base_url=str(self._settings.base_url),
            timeout_seconds=self._settings.model_timeout_seconds,
            thinking_mode=self._settings.provider_thinking_mode,
        )

    def _search_provider(self):
        if self._settings.search_provider == "brave":
            if self._settings.search_api_key is None:
                raise RuntimeError("search provider configuration was not validated")
            return BraveSearchProvider(self._settings.search_api_key)
        return MockSearchProvider(
            [
                SearchResult(
                    title="EvoAgent 离线演示资料",
                    url="https://example.com/evoagent-demo",
                    snippet="用于不访问公网的确定性搜索结果。",
                    source="mock",
                )
            ]
        )


async def run_worker() -> None:
    settings = Settings()
    embedding_provider = provider_from_settings(settings)
    client = redis_client(settings)
    wakeup = Wakeup(client, settings.redis_namespace)
    async with Database(settings.database_url.get_secret_value()) as database:
        manager = JobLeaseManager(
            database.session_factory,
            lease_seconds=settings.lease_seconds,
            runtime_experiment_id=settings.worker_runtime_experiment_id,
            runtime_arm=settings.worker_runtime_arm,
        )
        database.session_factory.configure(info={"wakeup": wakeup})
        worker = JobWorker(
            worker_id=settings.worker_id,
            lease_manager=manager,
            handler=ConfiguredTaskHandler(settings, database, ServiceGate(settings, client)),
            concurrency=settings.worker_concurrency,
            wakeup=wakeup,
            heartbeat_seconds=settings.heartbeat_seconds,
            poll_seconds=settings.worker_poll_seconds,
            snapshot_schema_version=settings.snapshot_schema_version,
        )
        loop = asyncio.get_running_loop()
        with suppress(NotImplementedError):
            loop.add_signal_handler(signal.SIGTERM, worker.stop)
        try:
            await worker.run_forever()
        finally:
            if client is not None:
                await client.aclose()
            if hasattr(embedding_provider, "aclose"):
                await embedding_provider.aclose()


def main() -> None:
    with suppress(KeyboardInterrupt):
        asyncio.run(run_worker())


async def run_maintenance_worker():
    settings = Settings()
    provider = provider_from_settings(settings)
    client = redis_client(settings)
    task = asyncio.current_task()
    with suppress(NotImplementedError):
        asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, task.cancel)
    async with Database(settings.database_url.get_secret_value()) as database:
        worker = MaintenanceWorker(
            database.session_factory,
            LocalArtifactStore(settings.artifact_root),
            IndexService(
                database.session_factory,
                provider,
                settings.embedding_model,
                service_gate=ServiceGate(settings, client),
                dimension=settings.embedding_dimension,
                preprocessing=settings.embedding_preprocessing,
            ),
        )
        try:
            while True:
                from sqlalchemy.exc import SQLAlchemyError

                try:
                    handled = await worker.run_once()
                except SQLAlchemyError:
                    handled = False
                if not handled:
                    await asyncio.sleep(settings.worker_poll_seconds)
        finally:
            if client is not None:
                await client.aclose()
            if hasattr(provider, "aclose"):
                await provider.aclose()


def maintenance_main():
    with suppress(KeyboardInterrupt, asyncio.CancelledError):
        asyncio.run(run_maintenance_worker())
