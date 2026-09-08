"""Skill 聚合、版本与来源查询。"""

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from evoagent.db.models import SkillRecord, SkillVersionRecord
from evoagent.db.repositories.base import RecordNotFoundError
from evoagent.skills.lifecycle import SkillStatus, SkillVersionStatus


class SkillRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def add(self, skill: SkillRecord) -> None:
        self._session.add(skill)

    async def get(self, skill_id: UUID, *, for_update: bool = False) -> SkillRecord:
        statement = select(SkillRecord).where(SkillRecord.id == skill_id)
        if for_update:
            statement = statement.with_for_update()
        record = await self._session.scalar(statement)
        if record is None:
            raise RecordNotFoundError(f"skill does not exist: {skill_id}")
        return record

    async def find_by_slug(self, slug: str) -> SkillRecord | None:
        return await self._session.scalar(select(SkillRecord).where(SkillRecord.slug == slug))

    async def next_version(self, skill_id: UUID) -> int:
        value = await self._session.scalar(
            select(func.coalesce(func.max(SkillVersionRecord.version), 0)).where(
                SkillVersionRecord.skill_id == skill_id
            )
        )
        return int(value or 0) + 1

    async def active_versions(self) -> tuple[tuple[SkillRecord, SkillVersionRecord], ...]:
        rows = await self._session.execute(
            select(SkillRecord, SkillVersionRecord)
            .join(SkillVersionRecord, SkillVersionRecord.id == SkillRecord.active_version_id)
            .where(
                SkillRecord.status == SkillStatus.ENABLED,
                SkillVersionRecord.lifecycle_status == SkillVersionStatus.ACTIVE,
            )
            .order_by(SkillRecord.slug)
        )
        return tuple(rows)


class SkillVersionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def add(self, version: SkillVersionRecord) -> None:
        self._session.add(version)

    async def get(self, version_id: UUID) -> SkillVersionRecord:
        record = await self._session.get(SkillVersionRecord, version_id)
        if record is None:
            raise RecordNotFoundError(f"skill version does not exist: {version_id}")
        return record

    async def find_by_extraction_key(self, key: str) -> SkillVersionRecord | None:
        return await self._session.scalar(
            select(SkillVersionRecord).where(SkillVersionRecord.extraction_key == key)
        )
