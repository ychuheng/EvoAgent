"""ToolEffect 的语义幂等记录查询。"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from evoagent.db.models import ToolEffectRecord


class ToolEffectRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def add(self, effect: ToolEffectRecord) -> None:
        self._session.add(effect)

    async def find(self, effect_scope: str, semantic_key: str) -> ToolEffectRecord | None:
        return await self._session.scalar(
            select(ToolEffectRecord).where(
                ToolEffectRecord.effect_scope == effect_scope,
                ToolEffectRecord.semantic_key == semantic_key,
            )
        )
