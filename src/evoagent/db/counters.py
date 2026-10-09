"""事务内原子分配聚合序号；回滚同时撤销分配，不通过 MAX 推断下一项。"""

from uuid import UUID

from sqlalchemy import update


async def allocate(session, record_type, record_id: UUID, field: str) -> int:
    from evoagent.db.repositories.base import RecordNotFoundError

    column = getattr(record_type, field)
    result = await session.scalar(
        update(record_type)
        .where(record_type.id == record_id)
        .values({field: column + 1})
        .returning(column - 1)
    )
    if result is None:
        raise RecordNotFoundError(f"counter aggregate does not exist: {record_id}")
    return int(result)
