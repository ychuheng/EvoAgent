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
from evoagent.db.models import RunRecord, TaskRecord
from evoagent.db.session import Database
from evoagent.memory.maintenance import MaintenanceWorker
from evoagent.projects.inputs import InputChangedError
from evoagent.projects.readiness import REASON_UNOBSERVABLE, ProjectCommandReadinessProbe
from evoagent.projects.schema import ProjectAuthorizationRevoked
from evoagent.providers.base import ModelProvider
from evoagent.providers.mock import MockProvider
from evoagent.providers.openai_compatible import OpenAICompatibleProvider
from evoagent.retrieval.embeddings import provider_from_settings
from evoagent.retrieval.indexing import IndexService
from evoagent.runtime.budget import BudgetScope
from evoagent.runtime.persistent_runner import PersistentAgentRunner
from evoagent.sandbox.client import ControllerExecutor, DisabledSandboxExecutor
from evoagent.tasks.lease import JobLease, JobLeaseManager, TaskExecutionResult
from evoagent.tasks.lease_guard import LeaseGuard
from evoagent.tasks.state_machine import PersistentRunStatus
from evoagent.tools.builtin.artifact_read import ArtifactReadTool
from evoagent.tools.builtin.artifact_write import ArtifactWriteTool
from evoagent.tools.builtin.ask_user import AskUserTool
from evoagent.tools.builtin.calculator import CalculatorTool
from evoagent.tools.builtin.file_write import FileWriteTool
from evoagent.tools.builtin.shell import ShellTool
from evoagent.tools.builtin.web_fetch import WebFetchTool
from evoagent.tools.builtin.web_search import (
    BraveSearchProvider,
    DDGSSearchProvider,
    MockSearchProvider,
    SearchResult,
    WebSearchTool,
)
from evoagent.tools.guards import URLGuard
from evoagent.tools.output_store import ToolOutputStore
from evoagent.tools.registry import ToolRegistry
from evoagent.tools.sandbox import RunSandbox
from evoagent.trace.artifacts import ArtifactService, LocalArtifactStore
from evoagent.trace.notifications import RunEventNotifier
from evoagent.workers.main import JobWorker
from evoagent.workers.rate_limit import BudgetedProvider, GatedProvider, ServiceGate
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
        from evoagent.projects.boundaries import authorization_guard, resolve_run_project

        async with self._database.session_factory() as session:
            run = await session.get(RunRecord, lease.run_id)
            task = await session.get(TaskRecord, lease.task_id)
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
        project = (
            await resolve_run_project(
                self._database.session_factory,
                project_id=task.project_id,
                expected_authorization_version=task.project_authorization_version,
            )
            if task is not None
            else None
        )
        provider = self._provider(lease.run_id)

        async def check():
            async with self._database.session_factory() as session:
                await LeaseGuard(lease).check(session)

        gated = GatedProvider(provider, self._gate, f"model:{self._settings.model}", check)
        # Mock Provider 不产生真实费用，因此不经过预算闸门；付费路径才需要额度。
        if self._settings.provider is not ProviderName.MOCK:
            gated = BudgetedProvider(
                gated,
                settings=self._settings,
                session_factory=self._database.session_factory,
                scope=self._budget_scope(),
                task_id=lease.task_id,
                run_id=lease.run_id,
                provider_name=self._settings.provider.value,
                model=expected_model,
            )
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
                *self._project_tools(project),
                FileWriteTool(
                    RunSandbox(self._settings.artifact_root, lease.run_id), artifact_service
                ),
                ArtifactWriteTool(lease.run_id, artifact_service),
                ArtifactReadTool(
                    ToolOutputStore(
                        lease.run_id,
                        artifact_service,
                        self._database.session_factory,
                        settings=self._settings,
                    )
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
            authorization_check=(
                authorization_guard(
                    self._database.session_factory,
                    project_id=task.project_id,
                    expected_authorization_version=task.project_authorization_version,
                )
                if task is not None
                else None
            ),
        )
        try:
            return await runner.handle(lease)
        except ProjectAuthorizationRevoked as error:
            return TaskExecutionResult(
                status=PersistentRunStatus.AUTHORIZATION_REVOKED,
                error_code="authorization_revoked",
                error_message=str(error),
            )
        except InputChangedError as error:
            # F-02：输入在运行中被替换，明确终止而不是继续用新内容。
            return TaskExecutionResult(
                status=PersistentRunStatus.INPUT_CHANGED,
                error_code="input_changed",
                error_message=str(error),
            )
        finally:
            await web_fetch.aclose()
            close_search = getattr(search_provider, "aclose", None)
            if close_search is not None:
                await close_search()
            if isinstance(provider, OpenAICompatibleProvider):
                await provider.aclose()

    def _budget_scope(self) -> BudgetScope:
        """试跑与正式分账：由维护者显式选择，默认为正式（额度更严的那一个）。"""

        raw = self._settings.budget_scope.strip().lower()
        return BudgetScope.TRIAL if raw == BudgetScope.TRIAL.value else BudgetScope.FORMAL

    def _project_tools(self, project):
        """装配项目工具；没有绑定项目时不提供任何项目工具。

        实施计划 §6/§7：项目上下文与文件内容只通过这些工具进入，Agent 不能自行登记
        任意宿主路径；写工具只在 `read_write` 授权下注册，只读时模型连工具都看不到。
        """

        if project is None:
            return []
        from evoagent.tools.builtin.find_files import FindFilesTool
        from evoagent.tools.builtin.list_dir import ListDirTool
        from evoagent.tools.builtin.project_command import project_command_tools
        from evoagent.tools.builtin.project_edit import project_edit_tools
        from evoagent.tools.builtin.project_extract import project_extract_tools
        from evoagent.tools.builtin.project_file_read import ProjectFileReadTool
        from evoagent.tools.builtin.project_organize import project_organize_tools
        from evoagent.tools.builtin.search_text import SearchTextTool

        root = project.root
        # 命令工具只在**本环境真的能观测进程树**时注册（K2 / §13.4）：
        # 看不见会话进程数就没法实施配额，此时"不登记就绪、不接项目命令"，
        # 而不是让命令在无配额的情况下跑。宿主上的预检不能替代这里的判断。
        readiness = ProjectCommandReadinessProbe().check(self._settings)
        command_tools = (
            []
            if readiness.reason == REASON_UNOBSERVABLE
            else project_command_tools(
                root,
                authorization=project.authorization,
                allowlist=self._settings.project_command_allowlist,
                timeout_seconds=self._settings.project_command_timeout_seconds,
                output_bytes=self._settings.project_command_output_bytes,
                memory_bytes=self._settings.project_command_memory_bytes,
                max_processes=self._settings.project_command_max_processes,
                environment=self._settings.project_command_environment,
                trusted_host_mode=self._settings.trusted_host_mode,
            )
        )
        return [
            ListDirTool(root),
            FindFilesTool(root),
            SearchTextTool(root),
            ProjectFileReadTool(root),
            # F-03：非 UTF-8 文本与文本型 PDF 的抽取是只读能力，只读授权下也提供。
            *project_extract_tools(root),
            *project_edit_tools(root, authorization=project.authorization),
            # F-05：目录整理是写操作，只读授权下不注册（删除仍走 delete_file 单独审批）。
            *project_organize_tools(root, authorization=project.authorization),
            *command_tools,
        ]

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
        if self._settings.search_provider == "ddgs":
            return DDGSSearchProvider(timeout_seconds=self._settings.tool_timeout_seconds)
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
    wakeup = Wakeup(
        client,
        settings.redis_namespace,
        post_commit_hooks_enabled=settings.runtime_maintenance_idle_backoff_enabled,
    )
    notifier = (
        RunEventNotifier(client, settings.redis_namespace)
        if settings.runtime_shared_notifications_enabled
        else None
    )
    from evoagent.workers.presence import WorkerPresence

    presence = WorkerPresence(client, settings.redis_namespace)
    async with Database(settings.database_url.get_secret_value()) as database:
        manager = JobLeaseManager(
            database.session_factory,
            lease_seconds=settings.lease_seconds,
            runtime_experiment_id=settings.worker_runtime_experiment_id,
            runtime_arm=settings.worker_runtime_arm,
        )
        database.session_factory.configure(
            info={
                "wakeup": wakeup,
                **({"event_notifier": notifier} if notifier is not None else {}),
            }
        )
        worker = JobWorker(
            worker_id=settings.worker_id,
            lease_manager=manager,
            handler=ConfiguredTaskHandler(settings, database, ServiceGate(settings, client)),
            concurrency=settings.worker_concurrency,
            wakeup=wakeup,
            presence=presence,
            heartbeat_seconds=settings.heartbeat_seconds,
            poll_seconds=settings.worker_poll_seconds,
            snapshot_schema_version=settings.snapshot_schema_version,
            recovery_scan_decoupled=settings.runtime_recovery_scan_decoupled_enabled,
            maintenance_idle_backoff=settings.runtime_maintenance_idle_backoff_enabled,
            heartbeat_status_merge=settings.runtime_heartbeat_status_merge_enabled,
        )
        loop = asyncio.get_running_loop()
        with suppress(NotImplementedError):
            loop.add_signal_handler(signal.SIGTERM, worker.stop)
        try:
            await worker.run_forever()
        finally:
            if notifier is not None:
                await notifier.close()
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
    notifier = (
        RunEventNotifier(client, settings.redis_namespace)
        if settings.runtime_shared_notifications_enabled
        else None
    )
    wakeup = Wakeup(
        client,
        settings.redis_namespace,
        post_commit_hooks_enabled=settings.runtime_maintenance_idle_backoff_enabled,
    )
    task = asyncio.current_task()
    with suppress(NotImplementedError):
        asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, task.cancel)
    async with Database(settings.database_url.get_secret_value()) as database:
        database.session_factory.configure(
            info={
                **({"wakeup": wakeup} if settings.runtime_maintenance_idle_backoff_enabled else {}),
                **({"event_notifier": notifier} if notifier is not None else {}),
            }
        )
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
        from evoagent.learning.bootstrap import assemble_learning

        learning_handler, learning_provider = assemble_learning(
            database.session_factory, worker.store, settings, ServiceGate(settings, client)
        )
        handlers = {
            "learning_revoke": learning_handler,
            "learning_budget_reconcile": learning_handler,
        }
        if settings.learning_enabled:
            handlers.update(learning_propose=learning_handler, learning_validate=learning_handler)
        worker.handlers.update(handlers)
        worker.allowed_kinds = worker.allowed_kinds | handlers.keys()
        listener = None
        lanes = []
        try:
            if settings.runtime_maintenance_idle_backoff_enabled or settings.learning_enabled:
                from evoagent.workers.maintenance import MaintenanceLane

                stopping = asyncio.Event()
                critical = MaintenanceWorker(
                    database.session_factory,
                    worker.store,
                    worker.index_service,
                    lane="critical",
                    handlers=handlers,
                )
                background = MaintenanceWorker(
                    database.session_factory,
                    worker.store,
                    worker.index_service,
                    lane="background",
                    allowed_kinds={"archive", "index_source", "index_rebuild"},
                )
                listener = asyncio.create_task(wakeup.listen())
                lanes = [
                    asyncio.create_task(
                        MaintenanceLane(
                            critical,
                            wakeup,
                            poll_seconds=min(settings.worker_poll_seconds, 1),
                            drain_immediately=True,
                        ).run(stopping)
                    ),
                    asyncio.create_task(
                        MaintenanceLane(
                            background,
                            wakeup,
                            poll_seconds=1,
                            idle_backoff=settings.runtime_maintenance_idle_backoff_enabled,
                        ).run(stopping)
                    ),
                ]
                if settings.learning_enabled:
                    lanes.append(asyncio.create_task(learning_handler.periodic_reconciliation()))
                    learning = MaintenanceWorker(
                        database.session_factory,
                        worker.store,
                        handlers=handlers,
                        allowed_kinds={"learning_propose", "learning_validate"},
                    )
                    lanes.append(
                        asyncio.create_task(
                            MaintenanceLane(
                                learning,
                                wakeup,
                                poll_seconds=1,
                                idle_backoff=settings.runtime_maintenance_idle_backoff_enabled,
                                drain_immediately=True,
                            ).run(stopping)
                        )
                    )
                await asyncio.gather(*lanes)
                return
            while True:
                from sqlalchemy.exc import SQLAlchemyError

                try:
                    handled = await worker.run_once()
                except SQLAlchemyError:
                    handled = False
                if not handled:
                    await asyncio.sleep(settings.worker_poll_seconds)
        finally:
            if listener is not None:
                lanes.append(listener)
            for lane in lanes:
                lane.cancel()
            await asyncio.gather(*lanes, return_exceptions=True)
            await wakeup.close()
            if notifier is not None:
                await notifier.close()
            if client is not None:
                await client.aclose()
            if hasattr(provider, "aclose"):
                await provider.aclose()
            if learning_provider is not None:
                await learning_provider.aclose()


def maintenance_main():
    with suppress(KeyboardInterrupt, asyncio.CancelledError):
        asyncio.run(run_maintenance_worker())
