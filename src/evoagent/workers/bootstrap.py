"""根据配置组装并启动独立 Worker 进程。"""

import asyncio
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
from evoagent.db.session import Database
from evoagent.providers.base import ModelProvider
from evoagent.providers.mock import MockProvider
from evoagent.providers.openai_compatible import OpenAICompatibleProvider
from evoagent.runtime.persistent_runner import PersistentAgentRunner
from evoagent.tasks.lease import JobLease, JobLeaseManager, TaskExecutionResult
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
from evoagent.tools.registry import ToolRegistry
from evoagent.tools.sandbox import RunSandbox, ShellSandbox
from evoagent.trace.artifacts import ArtifactService, LocalArtifactStore
from evoagent.workers.main import JobWorker


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

    def __init__(self, settings: Settings, database: Database) -> None:
        self._settings = settings
        self._database = database

    async def handle(self, lease: JobLease) -> TaskExecutionResult:
        provider = self._provider(lease.run_id)
        search_provider = self._search_provider()
        web_fetch = WebFetchTool(URLGuard(), timeout_seconds=self._settings.tool_timeout_seconds)
        artifact_service = ArtifactService(
            LocalArtifactStore(self._settings.artifact_root), self._database.session_factory
        )
        registry = ToolRegistry(
            [
                CalculatorTool(),
                FileReadTool(self._settings.workspace),
                FileWriteTool(RunSandbox(self._settings.artifact_root, lease.run_id)),
                ArtifactWriteTool(lease.run_id, artifact_service),
                WebSearchTool(search_provider),
                web_fetch,
                AskUserTool(),
                ShellTool(
                    ShellSandbox(
                        self._settings.workspace,
                        allowed_executables=self._settings.shell_allowed_executables,
                        timeout_seconds=self._settings.tool_timeout_seconds,
                    )
                ),
            ]
        )
        runner = PersistentAgentRunner(
            settings=self._settings,
            session_factory=self._database.session_factory,
            context_builder=ContextBuilder(),
            provider=provider,
            registry=registry,
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
    async with Database(settings.database_url.get_secret_value()) as database:
        manager = JobLeaseManager(database.session_factory, lease_seconds=settings.lease_seconds)
        worker = JobWorker(
            worker_id=settings.worker_id,
            lease_manager=manager,
            handler=ConfiguredTaskHandler(settings, database),
            heartbeat_seconds=settings.heartbeat_seconds,
            poll_seconds=settings.worker_poll_seconds,
        )
        await worker.run_forever()


def main() -> None:
    with suppress(KeyboardInterrupt):
        asyncio.run(run_worker())
