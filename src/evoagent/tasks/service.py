"""Task 聚合的应用服务与事务边界。"""

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from pydantic import TypeAdapter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from evoagent.db.models import (
    DEFAULT_WORKSPACE_ID,
    ApprovalStatus,
    ProjectRecord,
    RunRecord,
    SessionRecord,
    TaskRecord,
    ToolApprovalRecord,
    WorkspaceRecord,
)
from evoagent.db.repositories.base import ConcurrentUpdateError, RecordNotFoundError
from evoagent.db.unit_of_work import UnitOfWork
from evoagent.projects.inputs import freeze_inputs
from evoagent.projects.schema import (
    ProjectAuthorizationError,
    ProjectNotFoundError,
    ProjectStatus,
)
from evoagent.projects.service import root_status
from evoagent.runtime.run_config import RunMode
from evoagent.sessions.service import append_message, project_terminal
from evoagent.skills.schema import TaskFamily
from evoagent.tasks.acceptance import AcceptanceSpec
from evoagent.tasks.state_machine import PersistentRunStatus, TaskStatus


class TaskServiceError(Exception):
    """Task Service 可以安全映射成 API 响应的业务错误。"""

    code = "task_service_error"


class TaskNotFoundError(TaskServiceError):
    code = "task_not_found"


class SessionNotFoundError(TaskServiceError):
    code = "session_not_found"


class TaskOperationConflictError(TaskServiceError):
    code = "task_operation_conflict"


@dataclass(frozen=True, slots=True)
class TaskAggregate:
    """API 查询需要的 Task 与最新 Run 快照。"""

    task: TaskRecord
    run: RunRecord


class TaskService:
    """协调 Task、Run 与事件，避免路由层直接操作 ORM。"""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def create_workspace(self, name: str) -> WorkspaceRecord:
        normalized = name.strip()
        if not normalized:
            raise ValueError("workspace name cannot be blank")
        async with UnitOfWork(self._session_factory) as unit:
            record = WorkspaceRecord(name=normalized)
            unit.session.add(record)
            await unit.session.flush()
            await unit.commit()
            return record

    async def list_workspaces(self) -> tuple[WorkspaceRecord, ...]:
        async with self._session_factory() as session:
            records = await session.scalars(
                select(WorkspaceRecord).order_by(WorkspaceRecord.created_at, WorkspaceRecord.id)
            )
            return tuple(records)

    async def create_session(
        self,
        title: str,
        workspace_id: UUID = DEFAULT_WORKSPACE_ID,
        *,
        project_id: UUID | None = None,
    ) -> SessionRecord:
        normalized = title.strip()
        if not normalized:
            raise ValueError("session title cannot be blank")
        async with UnitOfWork(self._session_factory) as unit:
            if await unit.session.get(WorkspaceRecord, workspace_id) is None:
                raise SessionNotFoundError(f"workspace does not exist: {workspace_id}")
            if project_id is not None and (
                await unit.session.get(ProjectRecord, project_id) is None
            ):
                raise ProjectNotFoundError(f"项目不存在：{project_id}")
            record = SessionRecord(
                title=normalized, workspace_id=workspace_id, project_id=project_id
            )
            unit.session.add(record)
            await unit.session.flush()
            await unit.commit()
            return record

    async def create_task(
        self,
        *,
        session_id: UUID,
        goal: str,
        acceptance: AcceptanceSpec | None = None,
        provider: str,
        model: str,
        run_mode: RunMode = RunMode.RETRIEVAL,
        project_id: UUID | None = None,
        project_override: bool = False,
        input_paths: list[str] | None = None,
        family: str | None = None,
    ) -> TaskAggregate:
        """在同一事务中创建 Task、首个 Run 和初始事件。

        项目绑定在**创建时冻结**：默认取会话当前选中的项目，也可以在创建时显式指定。
        冻结的是 `(project_id, authorization_version)`，因此：
        - 之后在页面上切换会话项目不影响在跑 Task；
        - 撤销授权后新 Task 不能进入旧根（这里直接拒绝）；
        - 在跑 Task 的下一次工具调用会因版本不一致被拒绝。

        `input_paths` 给出时，输入文件的**内容哈希**也在此刻冻结（F-02）：
        运行中文件被替换会以 `input_changed` 终止，而不是悄悄换掉输入。
        """

        family = TypeAdapter(TaskFamily | None).validate_python(family)
        normalized_goal = goal.strip()
        normalized_provider = provider.strip()
        normalized_model = model.strip()
        if not normalized_goal:
            raise ValueError("task goal cannot be blank")
        if not normalized_provider or not normalized_model:
            raise ValueError("provider and model cannot be blank")
        if run_mode is RunMode.PINNED_SKILL:
            raise ValueError("pinned skill runs may only be created by the evaluation service")

        async with UnitOfWork(self._session_factory) as unit:
            session = await unit.session.get(SessionRecord, session_id)
            if session is None:
                raise SessionNotFoundError(f"session does not exist: {session_id}")
            bound_project_id = (
                project_id if project_override or project_id is not None else session.project_id
            )
            authorization_version: int | None = None
            project_root: Path | None = None
            if bound_project_id is not None:
                project = await unit.session.get(ProjectRecord, bound_project_id)
                if project is None:
                    raise ProjectNotFoundError(f"项目不存在：{bound_project_id}")
                if project.status is ProjectStatus.REVOKED:
                    raise ProjectAuthorizationError("项目授权已撤销，不能在此项目下创建任务")
                # 目录不可用（挂载丢失、被删、被替换、权限不足）时不得创建新 Task：
                # 计划要求"撤销后新 Task 不得进入旧根"，不可用与撤销对用户是同一后果。
                current = root_status(project.root)
                if current != "available":
                    raise ProjectAuthorizationError(
                        f"项目根当前不可用（{current}），不能在此项目下创建任务；"
                        "请恢复目录后重新检查该项目"
                    )
                authorization_version = project.authorization_version
                project_root = Path(project.root)

            frozen_inputs = None
            if input_paths:
                if project_root is None:
                    raise ProjectAuthorizationError(
                        "指定输入文件需要先为该会话选择已授权项目，Agent 不扫描项目之外的目录"
                    )
                frozen_inputs = (await freeze_inputs(project_root, input_paths)).model_dump(
                    mode="json"
                )

            task = TaskRecord(
                session_id=session_id,
                project_id=bound_project_id,
                project_authorization_version=authorization_version,
                goal=normalized_goal,
                family=family,
                selection_contract_version=3,
                acceptance=acceptance.model_dump(mode="json") if acceptance else None,
                frozen_inputs=frozen_inputs,
                status=TaskStatus.QUEUED,
            )
            unit.tasks.add(task)
            await unit.session.flush()
            run = RunRecord(
                data_role="personal",
                task_id=task.id,
                status=PersistentRunStatus.QUEUED,
                provider=normalized_provider,
                model=normalized_model,
                run_mode=run_mode.value,
            )
            unit.runs.add(run)
            await unit.session.flush()
            message = await append_message(
                unit.session,
                task=task,
                run=run,
                kind="goal",
                role="user",
                content=normalized_goal,
            )
            task.history_before_sequence = message.session_sequence
            await unit.events.append(
                run_id=run.id,
                event_type="task.queued",
                payload={"task_id": str(task.id)},
                created_at=datetime.now(UTC),
            )
            await unit.commit()
            return TaskAggregate(task=task, run=run)

    async def get_task(self, task_id: UUID) -> TaskAggregate:
        async with UnitOfWork(self._session_factory) as unit:
            try:
                task = await unit.tasks.get(task_id)
            except RecordNotFoundError as error:
                raise TaskNotFoundError(str(error)) from error
            run = await unit.runs.latest_for_task(task_id)
            if run is None:
                raise TaskNotFoundError(f"task has no run: {task_id}")
            return TaskAggregate(task=task, run=run)

    async def list_sessions(self) -> tuple[SessionRecord, ...]:
        async with self._session_factory() as session:
            records = await session.scalars(
                select(SessionRecord).order_by(SessionRecord.created_at, SessionRecord.id)
            )
            return tuple(records)

    async def cancel_task(self, task_id: UUID) -> TaskAggregate:
        """运行中的任务只记录取消请求，由持有租约的 Worker 收尾。"""

        async with UnitOfWork(self._session_factory) as unit:
            try:
                task = await unit.tasks.get(task_id)
            except RecordNotFoundError as error:
                raise TaskNotFoundError(str(error)) from error
            run = await unit.runs.latest_for_task(task_id)
            if run is None:
                raise TaskNotFoundError(f"task has no run: {task_id}")
            if task.status in (TaskStatus.RUNNING, TaskStatus.WAITING_TOOL):
                task.cancel_requested = True
                task.lock_version += 1
                # I-04：取消必须立刻把等待中的审批结清，不能留下悬空的待办，
                # 否则页面会一直显示"等待决定"，而任务其实已经在收尾。
                await self._cancel_pending_approvals(unit, task.id)
                await unit.events.append(
                    run_id=run.id,
                    event_type="task.cancel_requested",
                    payload={"task_id": str(task.id)},
                    created_at=datetime.now(UTC),
                )
                await unit.commit()
                return TaskAggregate(task=task, run=run)
        return await self._change_state(
            task_id,
            task_target=TaskStatus.CANCELLED,
            run_target=PersistentRunStatus.CANCELLED,
            event_type="task.cancelled",
        )

    async def pause_task(self, task_id: UUID) -> TaskAggregate:
        return await self._change_state(
            task_id,
            task_target=TaskStatus.PAUSED,
            run_target=PersistentRunStatus.PAUSED,
            event_type="task.paused",
        )

    async def resume_task(self, task_id: UUID) -> TaskAggregate:
        return await self._change_state(
            task_id,
            task_target=TaskStatus.QUEUED,
            run_target=PersistentRunStatus.QUEUED,
            event_type="task.resumed",
        )

    async def _change_state(
        self,
        task_id: UUID,
        *,
        task_target: TaskStatus,
        run_target: PersistentRunStatus,
        event_type: str,
    ) -> TaskAggregate:
        async with UnitOfWork(self._session_factory) as unit:
            try:
                task = await unit.tasks.get(task_id)
            except RecordNotFoundError as error:
                raise TaskNotFoundError(str(error)) from error
            run = await unit.runs.latest_for_task(task_id)
            if run is None:
                raise TaskNotFoundError(f"task has no run: {task_id}")
            try:
                task = await unit.tasks.transition(
                    task.id,
                    expected_version=task.lock_version,
                    target=task_target,
                )
                run = await unit.runs.transition(
                    run.id,
                    expected_version=run.lock_version,
                    target=run_target,
                )
            except (ConcurrentUpdateError, ValueError) as error:
                raise TaskOperationConflictError(str(error)) from error
            if task_target is TaskStatus.CANCELLED:
                await self._cancel_pending_approvals(unit, task.id)
            await project_terminal(unit.session, task, run)
            await unit.events.append(
                run_id=run.id,
                event_type=event_type,
                payload={"task_id": str(task.id), "status": task_target.value},
                created_at=datetime.now(UTC),
            )
            await unit.commit()
            return TaskAggregate(task=task, run=run)

    @staticmethod
    async def _cancel_pending_approvals(unit: UnitOfWork, task_id: UUID) -> None:
        """把该 Task 下仍在等待的审批标为已取消，并记录决定时间。"""

        approvals = await unit.session.scalars(
            select(ToolApprovalRecord).where(
                ToolApprovalRecord.task_id == task_id,
                ToolApprovalRecord.status == ApprovalStatus.PENDING,
            )
        )
        now = datetime.now(UTC)
        for approval in approvals:
            approval.status = ApprovalStatus.CANCELLED
            approval.decided_at = now
