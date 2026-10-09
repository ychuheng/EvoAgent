"""授权项目的应用服务：登记、选择、检查、改级与撤销。

设计约束对应实施计划 §6 的 P-01/P-02：

- 根路径在登记时规范化并冻结；模型不能自行登记宿主路径（登记只经 API 入口）。
- 撤销或改级都自增 `authorization_version` 并写一条不可改写的 `project_events`。
- Worker 侧每次工具调用都用 `resolve_active_project` 重新读取当前授权，
  **不是**在 Task 创建时缓存一个"允许"的结论。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from evoagent.db.models import (
    DEFAULT_WORKSPACE_ID,
    ProjectEventRecord,
    ProjectRecord,
    SessionRecord,
    TaskRecord,
)
from evoagent.db.unit_of_work import UnitOfWork
from evoagent.projects.schema import (
    NormalizedRoot,
    ProjectAuthorization,
    ProjectAuthorizationError,
    ProjectAuthorizationRevoked,
    ProjectNotFoundError,
    ProjectRegistrationError,
    ProjectStatus,
    normalize_root,
)


@dataclass(frozen=True, slots=True)
class ActiveProject:
    """一次工具调用被允许使用的项目根。"""

    id: UUID
    name: str
    root: Path
    authorization: ProjectAuthorization
    authorization_version: int

    @property
    def writable(self) -> bool:
        return self.authorization is ProjectAuthorization.READ_WRITE


def root_status(root: str) -> str:
    """实测根目录当前状态（计划 §6 P-02：区分目录不可用、权限不足、挂载缺失）。

    返回 `available` / `missing` / `not_a_directory` / `permission_denied` / `unreadable`：
    只报"可用/不可用"不够——挂载丢失、目标被删、权限不足对用户的处置完全不同。
    """

    path = Path(root)
    if not path.exists():
        # 挂载点不存在，或目录已被删除；对用户都是"这个根现在进不去"。
        return "missing"
    if not path.is_dir():
        return "not_a_directory"
    try:
        next(os.scandir(path), None)
    except PermissionError:
        return "permission_denied"
    except OSError:
        return "unreadable"
    return "available"


def _root_available(root: str) -> bool:
    return root_status(root) == "available"


class ProjectService:
    """项目登记与授权的事务边界。"""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def register(
        self,
        *,
        path: str,
        name: str | None = None,
        authorization: ProjectAuthorization = ProjectAuthorization.READ,
        workspace_id: UUID = DEFAULT_WORKSPACE_ID,
    ) -> ProjectRecord:
        normalized = normalize_root(path)
        display_name = (name or "").strip() or Path(normalized.display).name
        if not display_name:
            raise ProjectRegistrationError("项目名不能为空")

        async with UnitOfWork(self._session_factory) as unit:
            existing = await unit.session.scalar(
                select(ProjectRecord).where(ProjectRecord.root == normalized.display)
            )
            if existing is not None:
                # 重复登记同一物理根是幂等的：不新建记录，只把已撤销的项目恢复为可用。
                if existing.status is ProjectStatus.REVOKED:
                    existing.status = ProjectStatus.AVAILABLE
                    existing.authorization = authorization
                    existing.authorization_version += 1
                    existing.lock_version += 1
                    await self._append_event(
                        unit, existing, event_type="project.reauthorized", payload={}
                    )
                    await unit.commit()
                return existing

            record = ProjectRecord(
                name=display_name,
                root=normalized.display,
                authorization=authorization,
                status=ProjectStatus.AVAILABLE,
                authorization_version=1,
                workspace_id=workspace_id,
            )
            unit.session.add(record)
            await unit.session.flush()
            await self._append_event(
                unit,
                record,
                event_type="project.registered",
                payload={"name": display_name},
            )
            await unit.commit()
            return record

    async def list_projects(self) -> tuple[ProjectRecord, ...]:
        async with self._session_factory() as session:
            records = await session.scalars(
                select(ProjectRecord).order_by(ProjectRecord.created_at, ProjectRecord.id)
            )
            return tuple(records)

    async def get(self, project_id: UUID) -> ProjectRecord:
        async with self._session_factory() as session:
            record = await session.get(ProjectRecord, project_id)
        if record is None:
            raise ProjectNotFoundError(f"项目不存在：{project_id}")
        return record

    async def status_report(self, project_id: UUID) -> tuple[ProjectRecord, bool, str]:
        """返回项目记录、根是否可用、以及**具体状态**（缺挂载/被删/权限不足）。"""

        record = await self.get(project_id)
        status = root_status(record.root)
        return record, status == "available", status

    async def check(self, project_id: UUID) -> ProjectRecord:
        """重新实测根可用性并同步 `status`（可用性变化不改授权版本）。"""

        record = await self.get(project_id)
        if record.status is ProjectStatus.REVOKED:
            return record
        available = _root_available(record.root)
        target = ProjectStatus.AVAILABLE if available else ProjectStatus.UNAVAILABLE
        if record.status is not target:
            async with UnitOfWork(self._session_factory) as unit:
                fresh = await unit.session.get(ProjectRecord, project_id)
                if fresh is None:
                    raise ProjectNotFoundError(f"项目不存在：{project_id}")
                fresh.status = target
                fresh.lock_version += 1
                await unit.commit()
                return fresh
        return record

    async def set_authorization(
        self, project_id: UUID, authorization: ProjectAuthorization
    ) -> ProjectRecord:
        """提升或降低授权级别；自增授权版本，使在跑 Task 立即受影响。"""

        async with UnitOfWork(self._session_factory) as unit:
            record = await unit.session.scalar(
                select(ProjectRecord).where(ProjectRecord.id == project_id).with_for_update()
            )
            if record is None:
                raise ProjectNotFoundError(f"项目不存在：{project_id}")
            if record.status is ProjectStatus.REVOKED:
                raise ProjectAuthorizationError("项目授权已撤销，请先重新登记")
            if record.authorization is not authorization:
                record.authorization = authorization
                record.lock_version += 1
            record.authorization_version += 1
            await self._append_event(
                unit,
                record,
                event_type="project.authorization_changed",
                payload={"authorization": authorization.value},
            )
            await unit.commit()
            return record

    async def revoke(self, project_id: UUID, *, reason: str = "") -> ProjectRecord:
        """撤销授权：新 Task 不得再进入旧根；在跑 Task 下一次工具调用被拒绝。"""

        async with UnitOfWork(self._session_factory) as unit:
            record = await unit.session.scalar(
                select(ProjectRecord).where(ProjectRecord.id == project_id).with_for_update()
            )
            if record is None:
                raise ProjectNotFoundError(f"项目不存在：{project_id}")
            record.status = ProjectStatus.REVOKED
            record.authorization_version += 1
            record.lock_version += 1
            await self._append_event(
                unit,
                record,
                event_type="project.revoked",
                payload={"reason": reason},
            )
            await unit.commit()
            return record

    async def bind_session(self, session_id: UUID, project_id: UUID | None) -> SessionRecord:
        """会话选择项目；只影响之后创建的 Task。"""

        async with UnitOfWork(self._session_factory) as unit:
            session = await unit.session.get(SessionRecord, session_id)
            if session is None:
                raise ProjectNotFoundError(f"会话不存在：{session_id}")
            if project_id is not None:
                project = await unit.session.get(ProjectRecord, project_id)
                if project is None:
                    raise ProjectNotFoundError(f"项目不存在：{project_id}")
                if project.status is ProjectStatus.REVOKED:
                    raise ProjectAuthorizationError("项目授权已撤销，不能绑定到会话")
            session.project_id = project_id
            await unit.commit()
            return session

    async def _append_event(
        self,
        unit: UnitOfWork,
        record: ProjectRecord,
        *,
        event_type: str,
        payload: dict,
    ) -> None:
        from evoagent.db.counters import allocate

        sequence = await allocate(unit.session, ProjectRecord, record.id, "next_event_sequence")
        unit.session.add(
            ProjectEventRecord(
                project_id=record.id,
                sequence=sequence,
                event_type=event_type,
                authorization=record.authorization,
                authorization_version=record.authorization_version,
                payload=payload,
                created_at=datetime.now(UTC),
            )
        )


async def resolve_active_project(
    session: AsyncSession,
    *,
    project_id: UUID,
    expected_authorization_version: int | None = None,
) -> ActiveProject:
    """在**每次**工具调用时重新解析当前授权。

    `expected_authorization_version` 是 Task 创建时冻结的版本；不匹配说明授权在运行中变过，
    直接拒绝——这是"运行中撤销后再次调用工具必须被拒绝"的实现点。

    撤销/改级抛出 `ProjectAuthorizationRevoked`（必须终止运行）；目录不可用等
    临时性问题抛出 `ProjectAuthorizationError`（模型可以换做法或报告限制）。
    """

    record = await session.get(ProjectRecord, project_id)
    if record is None:
        raise ProjectAuthorizationRevoked(
            "项目已被移除，本次调用被拒绝",
            project_id=str(project_id),
            detail="project_missing",
        )
    if record.status is ProjectStatus.REVOKED:
        raise ProjectAuthorizationRevoked(
            "项目授权已被撤销，本次调用被拒绝",
            project_id=str(project_id),
            detail="revoked",
        )
    if (
        expected_authorization_version is not None
        and record.authorization_version != expected_authorization_version
    ):
        raise ProjectAuthorizationRevoked(
            "项目授权在运行中发生变化，本次调用被拒绝",
            project_id=str(project_id),
            detail="authorization_version_changed",
        )
    if record.status is ProjectStatus.UNAVAILABLE:
        raise ProjectAuthorizationError("项目目录当前不可用（挂载缺失或权限不足）")
    return ActiveProject(
        id=record.id,
        name=record.name,
        root=Path(record.root),
        authorization=record.authorization,
        authorization_version=record.authorization_version,
    )


async def load_task_project(session: AsyncSession, task: TaskRecord) -> ActiveProject | None:
    """按 Task 冻结的绑定解析项目；没有绑定则返回 None（表示没有项目上下文）。"""

    if task.project_id is None:
        return None
    return await resolve_active_project(
        session,
        project_id=task.project_id,
        expected_authorization_version=task.project_authorization_version,
    )


__all__ = [
    "ActiveProject",
    "NormalizedRoot",
    "ProjectAuthorizationError",
    "ProjectNotFoundError",
    "ProjectRegistrationError",
    "ProjectService",
    "load_task_project",
    "resolve_active_project",
    "root_status",
]
