"""供阶段三来源提炼使用的规范化 TraceBundle。"""

from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from evoagent.db.unit_of_work import UnitOfWork
from evoagent.runtime.run_config import RunConfigSnapshot
from evoagent.trace.service import TraceService


class TraceBundle(BaseModel):
    """不包含模型隐藏推理的结构化提炼输入。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    run_id: UUID
    task_id: UUID
    goal: str
    final_answer: str | None
    status: str
    turns: tuple[dict[str, Any], ...]
    tool_calls: tuple[dict[str, Any], ...]
    tool_effects: tuple[dict[str, Any], ...]
    artifacts: tuple[dict[str, Any], ...]
    validation_results: tuple[dict[str, Any], ...] = ()
    run_config: RunConfigSnapshot | None = None


class TraceBundleService:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def build(
        self,
        run_id: UUID,
        *,
        validation_results: tuple[dict[str, Any], ...] = (),
        run_config: RunConfigSnapshot | None = None,
    ) -> TraceBundle:
        trace = await TraceService(self._session_factory).get_run_trace(run_id)
        async with UnitOfWork(self._session_factory) as unit:
            task = await unit.tasks.get(trace.task_id)
            run = await unit.runs.get(run_id)
        if run_config is None and run.config_snapshot is not None:
            run_config = RunConfigSnapshot.model_validate(run.config_snapshot)
        return TraceBundle(
            run_id=trace.run_id,
            task_id=trace.task_id,
            goal=task.goal,
            final_answer=trace.final_answer,
            status=trace.status.value,
            turns=trace.turns,
            tool_calls=trace.tool_calls,
            tool_effects=trace.tool_effects,
            artifacts=trace.artifacts,
            validation_results=validation_results,
            run_config=run_config,
        )
