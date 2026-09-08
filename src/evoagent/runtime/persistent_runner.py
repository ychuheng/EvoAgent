"""把持久化租约、快照和阶段一 AgentLoop 连接起来。"""

import asyncio
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from evoagent.config import Settings
from evoagent.core.context import ContextBuilder
from evoagent.core.loop import AgentLoop
from evoagent.core.models import AgentLoopStatus, EventType
from evoagent.db.models import RunRecord, TaskRecord
from evoagent.providers.base import ModelProvider
from evoagent.runtime.checkpoints import PersistentCheckpointStore
from evoagent.runtime.retry import RetryPolicy
from evoagent.runtime.run_config import RunConfigSnapshot, RunMode, sha256_text
from evoagent.skills.canonical import content_hash
from evoagent.skills.rendering import SkillContextRenderer
from evoagent.skills.retrieval import SkillRetrievalService
from evoagent.tasks.lease import JobLease, LeaseLostError, TaskExecutionResult
from evoagent.tasks.state_machine import PersistentRunStatus
from evoagent.tools.approvals import ApprovalRequiredError
from evoagent.tools.effects import PersistentToolMiddleware
from evoagent.tools.executor import ToolExecutor
from evoagent.tools.policy import PermissionPolicy
from evoagent.tools.registry import ToolRegistry
from evoagent.trace.persistent_sink import PersistentEventSink


class PersistentAgentRunner:
    """为 Worker 执行一个已领取的 Run，并返回可事务提交的结果。"""

    def __init__(
        self,
        *,
        settings: Settings,
        session_factory: async_sessionmaker[AsyncSession],
        context_builder: ContextBuilder,
        provider: ModelProvider,
        registry: ToolRegistry,
        retry_policy: RetryPolicy | None = None,
        permission_policy: PermissionPolicy | None = None,
    ) -> None:
        self._settings = settings
        self._session_factory = session_factory
        self._context_builder = context_builder
        self._provider = provider
        self._registry = registry
        self._retry_policy = retry_policy or RetryPolicy(
            max_attempts=settings.max_retry_attempts,
            base_seconds=settings.retry_base_seconds,
            max_seconds=settings.retry_max_seconds,
            max_elapsed_seconds=settings.retry_max_elapsed_seconds,
            max_total_tokens=settings.max_total_tokens,
        )
        self._permission_policy = permission_policy or PermissionPolicy()

    async def handle(self, lease: JobLease) -> TaskExecutionResult:
        task, run = await self._load_owned_records(lease)
        matches = await SkillRetrievalService(
            self._session_factory,
            self._registry,
            top_k=self._settings.skill_retrieval_top_k,
            minimum_score=self._settings.skill_retrieval_min_score,
            max_risk=self._settings.skill_max_effective_risk,
        ).select(run.id, task.goal)
        skill_context = (
            "\n\n".join(SkillContextRenderer().render(item.document.definition) for item in matches)
            or None
        )
        selected = matches[0].document if matches else None
        skill_context_hash = (
            content_hash([item.document.content_hash for item in matches]) if matches else None
        )
        config_snapshot = RunConfigSnapshot(
            provider=run.provider,
            model=run.model,
            system_prompt_hash=sha256_text(self._context_builder.system_prompt),
            tool_manifest_hash=self._registry.manifest_hash(),
            policy_hash=self._permission_policy.manifest_hash(),
            max_iterations=self._settings.max_iterations,
            max_total_tokens=self._settings.max_total_tokens,
            max_repeated_tool_calls=self._settings.max_repeated_tool_calls,
            max_tool_result_chars=self._settings.max_tool_result_chars,
            model_timeout_seconds=self._settings.model_timeout_seconds,
            task_timeout_seconds=self._settings.task_timeout_seconds,
            tool_timeout_seconds=self._settings.tool_timeout_seconds,
            code_version=self._settings.code_version,
            skill_retrieval_top_k=self._settings.skill_retrieval_top_k,
            skill_retrieval_min_score=self._settings.skill_retrieval_min_score,
            run_mode=RunMode(run.run_mode),
            skill_version_id=selected.version_id if selected else None,
            skill_content_hash=selected.content_hash if selected else None,
            skill_context_hash=skill_context_hash,
        )
        await self._persist_run_config(run.id, config_snapshot)
        sink = PersistentEventSink(lease.run_id, self._session_factory)
        checkpoints = PersistentCheckpointStore(
            lease.run_id,
            self._session_factory,
            schema_version=self._settings.snapshot_schema_version,
        )
        resume_state = await checkpoints.load_latest()
        if resume_state is None:
            initial_messages = self._context_builder.build(task.goal, skill_context=skill_context)
        else:
            initial_messages = resume_state.messages
            await sink.emit(
                EventType.RECOVERY_COMPLETED,
                {
                    "completed_iterations": resume_state.completed_iterations,
                    "decision": "resume_from_snapshot",
                },
            )
        await sink.emit(
            EventType.RUN_STARTED,
            {
                "provider": run.provider,
                "model": run.model,
                "attempt": lease.attempt,
                "resumed": resume_state is not None,
            },
        )
        executor = ToolExecutor(
            self._registry,
            sink,
            timeout_seconds=self._settings.tool_timeout_seconds,
            max_result_chars=self._settings.max_tool_result_chars,
            middleware=PersistentToolMiddleware(
                task_id=lease.task_id,
                run_id=lease.run_id,
                session_factory=self._session_factory,
                policy=self._permission_policy,
            ),
        )
        loop = AgentLoop(
            self._provider,
            self._registry,
            executor,
            sink,
            model=run.model,
            max_iterations=self._settings.max_iterations,
            max_total_tokens=self._settings.max_total_tokens,
            max_repeated_tool_calls=self._settings.max_repeated_tool_calls,
            checkpoint_writer=checkpoints,
            context_hash=skill_context_hash or "",
        )
        try:
            async with asyncio.timeout(self._settings.task_timeout_seconds):
                result = await loop.run(initial_messages, resume_state=resume_state)
        except ApprovalRequiredError as error:
            return TaskExecutionResult(
                status=PersistentRunStatus.WAITING_USER,
                error_code="approval_required",
                error_message=str(error),
            )
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            return TaskExecutionResult(
                status=PersistentRunStatus.TIMEOUT,
                error_code="run_timeout",
                error_message=(f"run exceeded {self._settings.task_timeout_seconds:g} seconds"),
            )
        except Exception as error:
            return TaskExecutionResult(
                status=PersistentRunStatus.FAILED,
                error_code="persistent_runtime_error",
                error_message=f"persistent runtime failed: {type(error).__name__}",
            )

        if result.status is AgentLoopStatus.COMPLETED:
            return TaskExecutionResult(
                status=PersistentRunStatus.COMPLETED,
                final_answer=result.final_answer,
            )
        if result.status is AgentLoopStatus.LIMIT_REACHED:
            return TaskExecutionResult(
                status=PersistentRunStatus.LIMIT_REACHED,
                error_code=result.error_code,
                error_message=result.error_message,
            )
        error_code = result.error_code or "agent_loop_failed"
        created_at = task.created_at
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=UTC)
        decision = self._retry_policy.decide(
            error_code,
            attempt_count=task.attempt_count,
            elapsed_seconds=(datetime.now(UTC) - created_at).total_seconds(),
            total_tokens=result.usage.total_tokens if result.usage is not None else 0,
        )
        if decision.retry:
            return TaskExecutionResult(
                status=PersistentRunStatus.RETRYING,
                error_code=error_code,
                error_message=result.error_message,
                next_attempt_at=datetime.now(UTC) + timedelta(seconds=decision.delay_seconds or 0),
            )
        return TaskExecutionResult(
            status=PersistentRunStatus.FAILED,
            error_code=error_code,
            error_message=result.error_message or decision.reason,
        )

    async def _load_owned_records(self, lease: JobLease) -> tuple[TaskRecord, RunRecord]:
        async with self._session_factory() as session:
            task = await session.scalar(
                select(TaskRecord).where(
                    TaskRecord.id == lease.task_id,
                    TaskRecord.lease_owner == lease.owner,
                )
            )
            run = await session.get(RunRecord, lease.run_id)
            if task is None or run is None or run.task_id != lease.task_id:
                raise LeaseLostError(f"lease is no longer owned by {lease.owner}")
            return task, run

    async def _persist_run_config(self, run_id, snapshot: RunConfigSnapshot) -> None:
        async with self._session_factory() as session:
            run = await session.get(RunRecord, run_id)
            if run is None:
                raise LeaseLostError(f"run no longer exists: {run_id}")
            serialized = snapshot.model_dump(mode="json")
            digest = snapshot.content_hash()
            if run.config_hash is not None and run.config_hash != digest:
                raise ValueError("persisted run config no longer matches the runtime")
            run.config_snapshot = serialized
            run.config_hash = digest
            await session.commit()
