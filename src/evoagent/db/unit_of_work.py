"""把一次应用操作使用的 Repository 绑定到同一事务。"""

from types import TracebackType

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from evoagent.db.repositories import (
    EvalRepository,
    RunEventRepository,
    RunRepository,
    RunSnapshotRepository,
    SkillRepository,
    SkillVersionRepository,
    TaskRepository,
    ToolEffectRepository,
)


class UnitOfWork:
    """显式提交或回滚的异步工作单元。"""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def __aenter__(self) -> "UnitOfWork":
        self.session = self._session_factory()
        self.tasks = TaskRepository(self.session)
        self.runs = RunRepository(self.session)
        self.events = RunEventRepository(self.session)
        self.snapshots = RunSnapshotRepository(self.session)
        self.effects = ToolEffectRepository(self.session)
        self.skills = SkillRepository(self.session)
        self.skill_versions = SkillVersionRepository(self.session)
        self.evals = EvalRepository(self.session)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        try:
            if exc_type is not None:
                await self.session.rollback()
        finally:
            await self.session.close()

    async def commit(self) -> None:
        await self.session.commit()

    async def rollback(self) -> None:
        await self.session.rollback()
