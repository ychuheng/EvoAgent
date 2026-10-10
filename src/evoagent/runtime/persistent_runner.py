"""把持久化租约、快照和阶段一 AgentLoop 连接起来。"""

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from evoagent.config import Settings
from evoagent.core.context import ContextBuilder
from evoagent.core.context_policy import policy_from_settings
from evoagent.core.loop import AgentLoop
from evoagent.core.models import AgentLoopStatus, EventType, Message, MessageRole
from evoagent.db.models import RunRecord, SessionRecord, TaskRecord
from evoagent.mcp.adapter import register_run_tools
from evoagent.mcp.connections import ConnectionManager
from evoagent.mcp.schema import MCPError
from evoagent.mcp.service import MCPService
from evoagent.memory.repository import check_run_references
from evoagent.memory.schema import MemoryError
from evoagent.projects.inputs import InputChangedError
from evoagent.projects.schema import ProjectAuthorizationRevoked
from evoagent.providers.base import ModelProvider
from evoagent.runtime.checkpoints import PersistentCheckpointStore, SnapshotCompatibilityError
from evoagent.runtime.context_store import ContextStore
from evoagent.runtime.retry import INFRASTRUCTURE_RETRY_CODES, RetryPolicy
from evoagent.runtime.run_config import RunConfigSnapshot, RunMode, sha256_text
from evoagent.sessions.service import history_for_task
from evoagent.skills.canonical import content_hash
from evoagent.skills.rendering import SkillContextRenderer
from evoagent.skills.retrieval import SkillRetrievalService
from evoagent.tasks.acceptance import AcceptanceSpec, check_acceptance
from evoagent.tasks.lease import JobLease, LeaseLostError, TaskExecutionResult
from evoagent.tasks.lease_guard import LeaseGuard
from evoagent.tasks.state_machine import PersistentRunStatus
from evoagent.tools.approvals import ApprovalRequiredError
from evoagent.tools.effects import PersistentToolMiddleware
from evoagent.tools.executor import ToolExecutor
from evoagent.tools.output_store import ToolOutputStore
from evoagent.tools.policy import PermissionPolicy
from evoagent.tools.registry import ToolRegistry
from evoagent.trace.artifacts import ArtifactService, LocalArtifactStore
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
        service_gate=None,
        authorization_check=None,
        selection_scope=None,
        selection_inputs=None,
    ) -> None:
        self._settings = settings
        self._service_gate = service_gate
        self._session_factory = session_factory
        self._context_builder = context_builder
        self._provider = provider
        self._registry = registry
        self._base_registry = registry
        self._authorization_check = authorization_check
        self._selection_scope = selection_scope
        self._selection_inputs = selection_inputs
        self._configured_selection_scope = selection_scope
        self._configured_selection_inputs = selection_inputs
        self._configured_authorization_check = authorization_check
        self._retry_policy = retry_policy or RetryPolicy(
            max_attempts=settings.max_retry_attempts,
            base_seconds=settings.retry_base_seconds,
            max_seconds=settings.retry_max_seconds,
            max_elapsed_seconds=settings.retry_max_elapsed_seconds,
            max_total_tokens=settings.max_total_tokens,
        )
        self._permission_policy = permission_policy or PermissionPolicy()

    async def handle(self, lease: JobLease) -> TaskExecutionResult:
        self._registry = ToolRegistry(
            self._base_registry.get(name) for name in self._base_registry.names
        )
        manager = ConnectionManager(self._settings, lease=lease)
        try:
            await register_run_tools(
                MCPService(self._session_factory, self._settings, manager),
                self._registry,
                lease.run_id,
                LeaseGuard(lease),
            )
            return await self._handle_owned(lease)
        except SnapshotCompatibilityError:
            return TaskExecutionResult(
                status=PersistentRunStatus.FAILED,
                error_code="snapshot_incompatible",
                error_message="stored snapshot/config does not match this runtime",
            )
        except (MemoryError, MCPError) as error:
            return TaskExecutionResult(
                status=PersistentRunStatus.FAILED,
                error_code=error.code,
                error_message="frozen execution context is no longer available",
            )

        finally:
            await manager.aclose()

    async def _handle_owned(self, lease: JobLease) -> TaskExecutionResult:
        guard = LeaseGuard(lease)
        self._selection_scope = self._configured_selection_scope
        self._selection_inputs = self._configured_selection_inputs
        self._authorization_check = self._configured_authorization_check
        task, run = await self._load_owned_records(lease)
        ordinary_v3 = task.selection_contract_version == 3 and run.data_role == "personal"
        if ordinary_v3:
            from evoagent.projects.boundaries import authorization_guard
            from evoagent.skills.selection_snapshot import SkillSelectionScope

            async with self._session_factory() as session:
                chat = await session.get(SessionRecord, task.session_id)
            if chat is None:
                raise MemoryError("skill_selection_scope_invalid")
            if self._selection_scope is None:
                self._selection_scope = SkillSelectionScope(
                    workspace_id=chat.workspace_id, project_id=task.project_id
                )
                self._selection_inputs = task.frozen_inputs
            if self._authorization_check is None:
                project_check = authorization_guard(
                    self._session_factory,
                    project_id=task.project_id,
                    expected_authorization_version=task.project_authorization_version,
                )

                async def authorize_ordinary():
                    if project_check is not None:
                        await project_check()

                self._authorization_check = authorize_ordinary
        use_v3 = (
            ordinary_v3
            or self._selection_scope is not None
            or (run.config_snapshot is not None and run.config_snapshot.get("schema_version") == 3)
        )
        if use_v3 and (self._selection_scope is None or self._authorization_check is None):
            raise SnapshotCompatibilityError("v3 selection requires internal authorization")
        renderer_version = (
            (run.config_snapshot.get("skill_renderer_version") or 1) if run.config_snapshot else 2
        )
        async with self._session_factory() as session:
            await check_run_references(session, run.id)
        use_resolver = run.run_mode == "retrieval" and (
            self._settings.retrieval_backend == "hybrid"
            or self._settings.memory_retrieval_enabled
            or self._settings.archive_retrieval_enabled
        )
        resolved = None
        choice = None
        if use_v3:
            from evoagent.skills.selection import SkillSelector

            await self._authorization_check()
            selector = SkillSelector(
                self._session_factory,
                self._settings,
                self._registry,
                guard,
                self._selection_scope,
                frozen_inputs=self._selection_inputs,
            )
            selector.service_gate = self._service_gate
            if ordinary_v3:
                from evoagent.skills.selection_runtime import resolve_ordinary

                choice = await resolve_ordinary(selector, task, run)
            else:
                choice = await selector.resolve_pinned(task, run)
            if use_resolver and (
                self._settings.memory_retrieval_enabled or self._settings.archive_retrieval_enabled
            ):
                from evoagent.runtime.context_resolver import ContextResolver

                resolved = await ContextResolver(
                    self._session_factory,
                    self._settings,
                    self._registry,
                    guard,
                    self._context_builder,
                    service_gate=self._service_gate,
                    external_skill_choice=choice,
                ).resolve(task, run)
        elif use_resolver:
            from evoagent.runtime.context_resolver import ContextResolver

            resolved = await ContextResolver(
                self._session_factory,
                self._settings,
                self._registry,
                guard,
                self._context_builder,
                service_gate=self._service_gate,
            ).resolve(task, run)
        matches = (
            choice.matches
            if choice is not None
            else resolved.skills
            if resolved
            else await SkillRetrievalService(
                self._session_factory,
                self._registry,
                top_k=self._settings.skill_retrieval_top_k,
                minimum_score=self._settings.skill_retrieval_min_score,
                max_risk=self._settings.skill_max_effective_risk,
                lease_guard=guard,
            ).select(run.id, task.goal)
        )
        skill_context = (
            choice.text
            if choice is not None
            else resolved.skill_text
            if resolved
            else (
                "\n\n".join(
                    SkillContextRenderer(renderer_version).render(item.document.definition)
                    for item in matches
                )
                or None
            )
        )
        selected = matches[0].document if matches else None
        skill_context_hash = (
            sha256_text(choice.text)
            if choice is not None and choice.text is not None
            else content_hash([item.document.content_hash for item in matches])
            if matches
            else None
        )
        config_snapshot = RunConfigSnapshot(
            progress_write_mode=(
                run.config_snapshot.get("progress_write_mode")
                if run.config_snapshot
                else ("batched_v1" if self._settings.runtime_event_batching_enabled else None)
            ),
            skill_renderer_version=run.config_snapshot.get("skill_renderer_version")
            if run.config_snapshot
            else 2,
            selected_skills=[
                {
                    "version_id": str(item.document.version_id),
                    "content_hash": item.document.content_hash,
                }
                for item in matches
            ]
            if choice is None
            else choice.selections,
            selector_version="skill-selector-v1" if use_v3 else None,
            renderer_version=2 if use_v3 else None,
            skill_selection_hash=choice.selection_hash if choice is not None else None,
            under_test_skill_version_id=run.pinned_skill_version_id if use_v3 else None,
            retrieval=resolved.config if resolved else None,
            schema_version=3 if use_v3 else self._settings.snapshot_schema_version,
            summarizer="extractive-v1" if self._settings.snapshot_schema_version == 2 else None,
            provider=run.provider,
            provider_thinking_mode=self._settings.provider_thinking_mode,
            model=run.model,
            system_prompt_hash=sha256_text(self._context_builder.system_prompt),
            tool_manifest_hash=self._registry.manifest_hash(),
            policy_hash=self._permission_policy.manifest_hash(),
            max_iterations=self._settings.max_iterations,
            max_total_tokens=self._settings.max_total_tokens,
            max_repeated_tool_calls=self._settings.max_repeated_tool_calls,
            context_policy=policy_from_settings(self._settings).manifest()
            if self._settings.context_policy == "bounded"
            else None,
            max_output_tokens=(
                self._settings.max_output_tokens
                if self._settings.context_policy == "bounded"
                else self._settings.model_request_max_output_tokens
            ),
            max_tool_result_chars=self._settings.max_tool_result_chars,
            model_timeout_seconds=self._settings.model_timeout_seconds,
            task_timeout_seconds=self._settings.task_timeout_seconds,
            tool_timeout_seconds=self._settings.tool_timeout_seconds,
            code_version=self._settings.code_version,
            skill_retrieval_top_k=min(self._settings.skill_retrieval_top_k, 1)
            if use_v3
            else self._settings.skill_retrieval_top_k,
            skill_retrieval_min_score=self._settings.skill_retrieval_min_score,
            run_mode=RunMode(run.run_mode),
            skill_version_id=selected.version_id if selected else None,
            skill_content_hash=selected.content_hash if selected else None,
            skill_context_hash=skill_context_hash,
        )
        await self._persist_run_config(run.id, config_snapshot, guard)
        project_context = await self._project_context(task)
        owner = asyncio.current_task()

        def stop_execution(error: BaseException) -> None:
            if owner is not None and not owner.done():
                owner.cancel()

        sink = PersistentEventSink(
            lease.run_id,
            self._session_factory,
            lease_guard=guard,
            batch_progress=config_snapshot.progress_write_mode == "batched_v1",
            on_background_error=stop_execution,
        )
        try:
            return await self._execute_owned(
                lease,
                guard,
                task,
                run,
                resolved,
                skill_context,
                choice.selection_hash if choice is not None else skill_context_hash,
                project_context,
                sink,
            )
        except LeaseLostError:
            await sink.abort()
            raise
        finally:
            await sink.aclose()

    async def _execute_owned(
        self,
        lease,
        guard,
        task,
        run,
        resolved,
        skill_context,
        skill_context_hash,
        project_context,
        sink,
    ) -> TaskExecutionResult:
        if task.frozen_inputs:
            await sink.emit(
                EventType.INPUT_FROZEN,
                {
                    "files": [
                        {"path": item["path"], "sha256": item["sha256"], "kind": item["kind"]}
                        for item in task.frozen_inputs.get("files", [])
                    ],
                },
            )
        checkpoints = PersistentCheckpointStore(
            lease.run_id,
            self._session_factory,
            schema_version=self._settings.snapshot_schema_version,
            settings=self._settings,
            lease_guard=guard,
        )
        resume_state = await checkpoints.load_latest()
        if resume_state is None:
            initial_messages = self._context_builder.build(
                task.goal,
                skill_context=resolved.skill_text if resolved else skill_context,
                external_context=resolved.memory_texts if resolved else (),
                project_context=project_context,
            )
            from evoagent.evals.runtime import history_for_run

            history = await history_for_run(self._session_factory, run.id)
            if history is None:
                async with self._session_factory() as session:
                    records = await history_for_task(session, task)
                history = tuple(
                    Message(role=MessageRole(record.role), content=record.content)
                    for record in records
                    if record.kind in {"goal", "terminal"} and record.role in {"user", "assistant"}
                )
            initial_messages = (initial_messages[0], *history, *initial_messages[1:])
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
            service_gate=self._service_gate,
            lease_check=lambda: self._load_owned_records(lease),
            timeout_seconds=self._settings.tool_timeout_seconds,
            max_result_chars=self._settings.max_tool_result_chars,
            output_store=ToolOutputStore(
                lease.run_id,
                ArtifactService(
                    LocalArtifactStore(self._settings.artifact_root), self._session_factory, guard
                ),
                self._session_factory,
                settings=self._settings,
            ),
            middleware=PersistentToolMiddleware(
                task_id=lease.task_id,
                run_id=lease.run_id,
                session_factory=self._session_factory,
                policy=self._permission_policy,
                lease_guard=guard,
                authorization_check=self._authorization_check,
                input_check=self._input_check(task),
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
            context_policy=policy_from_settings(self._settings),
            context_store=ContextStore(
                self._session_factory,
                guard,
                LocalArtifactStore(self._settings.artifact_root),
                policy_from_settings(self._settings),
                history_before_sequence=task.history_before_sequence,
                settings=self._settings,
            )
            if self._settings.snapshot_schema_version == 2
            else None,
            max_output_tokens=(
                self._settings.max_output_tokens
                if self._settings.context_policy == "bounded"
                else self._settings.model_request_max_output_tokens
            ),
            checkpoint_writer=checkpoints,
            context_hash=skill_context_hash or "",
            instruction_provider=self._instruction_provider(lease.task_id),
            before_model_call=lambda: self._before_model_call(lease),
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
        except (asyncio.CancelledError, LeaseLostError):
            raise
        except ProjectAuthorizationRevoked:
            # 授权在运行中变化：不重试、不续跑，交给调用方落成明确终态；
            # 拒绝事件已由工具中间件写入 Run 事件，已执行的动作保留。
            raise
        except MemoryError:
            # Keep scope revocation distinct from an ordinary runtime failure.
            raise
        except InputChangedError:
            # F-02：冻结输入被替换同样有明确终态 `input_changed`。
            # 这里必须原样抛出——早先被下面的兜底 `except Exception` 吃掉，
            # 于是终态变成通用码 `persistent_runtime_error`，运维看不出"输入被换了"。
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
            if task.acceptance is not None:
                try:
                    spec = AcceptanceSpec.model_validate(task.acceptance)
                    outcome = await check_acceptance(
                        spec,
                        answer=result.final_answer or "",
                        run_id=run.id,
                        session_factory=self._session_factory,
                        artifact_root=self._settings.artifact_root,
                    )
                except (OSError, ValidationError) as error:
                    await sink.emit(
                        EventType.ACCEPTANCE_CHECKED,
                        {"passed": False, "error": type(error).__name__},
                    )
                    return TaskExecutionResult(
                        status=PersistentRunStatus.FAILED,
                        final_answer=result.final_answer,
                        error_code="acceptance_check_error",
                        error_message="task acceptance could not be checked",
                    )
                await sink.emit(
                    EventType.ACCEPTANCE_CHECKED,
                    {"passed": outcome.passed, "checks": list(outcome.checks)},
                )
                if not outcome.passed:
                    return TaskExecutionResult(
                        status=PersistentRunStatus.FAILED,
                        final_answer=result.final_answer,
                        error_code="acceptance_failed",
                        error_message="the answer did not satisfy the task acceptance conditions",
                    )
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
        if (
            error_code in INFRASTRUCTURE_RETRY_CODES
            and (datetime.now(UTC) - created_at).total_seconds()
            < self._settings.retry_max_elapsed_seconds
        ):
            return TaskExecutionResult(
                status=PersistentRunStatus.RETRYING,
                error_code=error_code,
                error_message=result.error_message,
                next_attempt_at=datetime.now(UTC)
                + timedelta(seconds=self._settings.retry_base_seconds),
            )
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

    def _input_check(self, task: TaskRecord):
        """返回复核冻结输入集回调；没有项目或没有输入集时返回 None（F-02）。"""

        if not task.frozen_inputs or task.project_id is None:
            return None
        from evoagent.projects.inputs import InputSet, verify_inputs

        frozen = InputSet.model_validate(task.frozen_inputs)

        async def check() -> None:
            root = await self._project_root(task)
            await asyncio.to_thread(verify_inputs, root, frozen)

        return check

    async def _project_root(self, task: TaskRecord) -> Path:
        from evoagent.projects.boundaries import resolve_run_project

        project = await resolve_run_project(
            self._session_factory,
            project_id=task.project_id,
            expected_authorization_version=task.project_authorization_version,
        )
        if project is None:  # pragma: no cover - 有 project_id 时不会发生
            raise ValueError("task has no project")
        return project.root

    def _instruction_provider(self, task_id):
        """返回"取走待注入指令"的回调（I-03）。

        取走与标记在同一个事务里完成，因此同一条指令不会在两个迭代里重复注入。
        """

        async def claim() -> tuple[str, ...]:
            from evoagent.sessions.service import claim_pending_instructions

            async with self._session_factory() as session:
                pending = await claim_pending_instructions(session, task_id=task_id)
                await session.commit()
                return pending

        return claim

    async def _project_context(self, task: TaskRecord) -> str | None:
        """按 Task 冻结的绑定构造有界项目上下文；没有绑定项目时返回 None。

        授权在运行中变化时这里可能抛出 `ProjectAuthorizationRevoked`，由调用方落成终态。
        """

        if task.project_id is None:
            return None
        from evoagent.projects.boundaries import resolve_run_project
        from evoagent.projects.context import build_project_context

        project = await resolve_run_project(
            self._session_factory,
            project_id=task.project_id,
            expected_authorization_version=task.project_authorization_version,
        )
        if project is None:
            return None
        return build_project_context(project)

    async def _before_model_call(self, lease):
        await self._load_owned_records(lease)
        if self._authorization_check is not None:
            await self._authorization_check()

    async def _load_owned_records(self, lease: JobLease) -> tuple[TaskRecord, RunRecord]:
        async with self._session_factory() as session:
            task, run = await LeaseGuard(lease).check(session)
            if task.cancel_requested:
                raise asyncio.CancelledError
            from evoagent.skills.selection import FormalSkillReader

            await check_run_references(session, run.id)
            if (
                run.data_role == "personal"
                and (run.config_snapshot or {}).get("schema_version") == 3
            ):
                from evoagent.skills.selection_boundary import check_ordinary_bindings

                await check_ordinary_bindings(session, task=task, run=run)
            else:
                await FormalSkillReader().check_run_bindings(session, task=task, run=run)
            return task, run

    async def _persist_run_config(
        self, run_id, snapshot: RunConfigSnapshot, guard: LeaseGuard
    ) -> None:
        async with self._session_factory() as session:
            _, run = await guard.check(session)
            if run is None:
                raise LeaseLostError(f"run no longer exists: {run_id}")
            if run.id != run_id:
                raise ValueError("config run does not match lease")
            serialized = snapshot.canonical_dict()
            digest = snapshot.content_hash()
            if run.config_hash is not None and run.config_hash != digest:
                raise SnapshotCompatibilityError(
                    "persisted run config no longer matches the runtime"
                )
            run.config_snapshot = serialized
            run.config_hash = digest
            await session.commit()
