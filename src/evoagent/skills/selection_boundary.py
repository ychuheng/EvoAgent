"""Read-only actual v3 binding checks at model/tool execution boundaries."""

from types import SimpleNamespace

from sqlalchemy import select

from evoagent.db.models import RetrievalBatchRecord, SessionRecord
from evoagent.memory.schema import MemoryError
from evoagent.runtime.run_config import RunConfigSnapshot
from evoagent.sessions.service import text_hash
from evoagent.skills.selection_runtime import restore
from evoagent.skills.selection_snapshot import SkillSelectionScope


async def check_ordinary_bindings(session, *, task, run):
    if run.data_role != "personal" or not run.config_snapshot:
        return  # initial preparation has no injectable config yet
    if run.config_snapshot.get("schema_version") != 3:
        return  # historical contracts are checked by their existing reader
    if task.selection_contract_version != 3 or run.run_mode not in {"retrieval", "baseline"}:
        raise MemoryError("skill_selection_scope_invalid")
    try:
        config = RunConfigSnapshot.model_validate(run.config_snapshot)
    except ValueError:
        raise MemoryError("skill_selection_corrupt") from None
    if (
        config.content_hash() != run.config_hash
        or config.provider != run.provider
        or config.model != run.model
        or str(config.run_mode) != str(run.run_mode)
        or config.under_test_skill_version_id is not None
        or not run.skill_selection_frozen
    ):
        raise MemoryError("skill_selection_corrupt")
    workspace_id = await session.scalar(
        select(SessionRecord.workspace_id).where(SessionRecord.id == task.session_id)
    )
    if workspace_id is None:
        raise MemoryError("skill_selection_scope_invalid")
    scope = SkillSelectionScope(workspace_id=workspace_id, project_id=task.project_id)
    batch = await session.scalar(
        select(RetrievalBatchRecord).where(
            RetrievalBatchRecord.run_id == run.id,
            RetrievalBatchRecord.purpose == "skill_selector",
        )
    )
    if (
        batch is None
        or batch.workspace_id != workspace_id
        or batch.session_id != task.session_id
        or batch.query_hash != text_hash(task.goal)
        or batch.config.get("scope") != scope.model_dump(mode="json")
        or batch.config.get("mode") != str(run.run_mode)
        or batch.config.get("selector_version") != config.selector_version
        or batch.config.get("renderer_version") != config.renderer_version
    ):
        raise MemoryError("skill_selection_corrupt")
    selector = SimpleNamespace(scope=scope, VERSION=config.selector_version, factory=None)
    choice = await restore(selector, session, batch)
    if (
        choice.selections != tuple(config.selected_skills)
        or choice.selection_hash != config.skill_selection_hash
        or (text_hash(choice.text) if choice.text else None) != config.skill_context_hash
    ):
        raise MemoryError("skill_selection_corrupt")
