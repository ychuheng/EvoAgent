"""为一次工具调用解析项目根：授权拒绝在这里升级为明确的运行终态。

Worker 侧使用方式：

- 循环开始前调用 `resolve_run_project` 取一次项目根用于**装配**工具；
- 每次工具调用前调用 `guard.check()` 重新解析授权；授权在运行中变化时抛
  `ProjectAuthorizationRevoked`，由上层落成 `authorization_revoked` 终态。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from evoagent.projects.service import ActiveProject, resolve_active_project


async def resolve_run_project(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    project_id: UUID | None,
    expected_authorization_version: int | None,
) -> ActiveProject | None:
    """装配工具时解析一次项目根；没有绑定项目则返回 None。"""

    if project_id is None:
        return None
    async with session_factory() as session:
        return await resolve_active_project(
            session,
            project_id=project_id,
            expected_authorization_version=expected_authorization_version,
        )


def authorization_guard(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    project_id: UUID | None,
    expected_authorization_version: int | None,
) -> Callable[[], Awaitable[ActiveProject | None]] | None:
    """返回"每次调用都重新解析授权"的检查函数；没有项目绑定时返回 None。"""

    if project_id is None:
        return None

    async def check() -> ActiveProject | None:
        async with session_factory() as session:
            return await resolve_active_project(
                session,
                project_id=project_id,
                expected_authorization_version=expected_authorization_version,
            )

    return check


__all__ = ["authorization_guard", "resolve_run_project"]
