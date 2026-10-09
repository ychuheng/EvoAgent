"""Shared permission checks for immutable skill bodies and their source graph."""

from uuid import UUID

from sqlalchemy import select

from evoagent.db.models import (
    ArtifactRecord,
    EvalCaseRecord,
    EvalRunRecord,
    LearningSourceRecord,
    ProjectRecord,
    RunRecord,
    SessionRecord,
    SkillRecord,
    SkillSourceRecord,
    SkillVersionRecord,
    TaskRecord,
)
from evoagent.memory.repository import check_run_references
from evoagent.privacy.redaction import detect_sensitive
from evoagent.skills.canonical import canonical_json, content_hash
from evoagent.skills.schema import SkillDefinition


class SkillAccessError(ValueError):
    """Stable error with no source body in its message."""


class SkillAccessPolicy:
    async def check(self, session, version_id, *, workspace_id, project_id=None):
        pending, seen = [(version_id, frozenset())], set()
        root = None
        while pending:
            current, ancestors = pending.pop()
            if current in ancestors:
                raise SkillAccessError("skill_source_graph_cycle")
            if current in seen:
                continue  # shared ancestors are legal
            seen.add(current)
            if len(seen) > 32:
                raise SkillAccessError("skill_source_graph_budget_exceeded")
            version = await session.get(SkillVersionRecord, current)
            skill = await session.get(SkillRecord, version.skill_id) if version else None
            if (
                skill is None
                or skill.workspace_id != workspace_id
                or skill.project_id not in (None, project_id)
                or content_hash(version.definition) != version.content_hash
            ):
                raise SkillAccessError("skill_scope_or_hash_invalid")
            try:
                definition = SkillDefinition.model_validate(version.definition)
            except ValueError as error:
                raise SkillAccessError("skill_definition_invalid") from error
            if (
                definition.schema_version not in {1, 2}
                or version.schema_version != definition.schema_version
            ):
                raise SkillAccessError("skill_schema_version_invalid")
            if detect_sensitive(canonical_json(definition.model_dump(mode="json"))):
                raise SkillAccessError("skill_sensitive_content")
            if root is None:
                root = version
            links = list(
                await session.scalars(
                    select(SkillSourceRecord)
                    .where(SkillSourceRecord.skill_version_id == current)
                    .limit(201)
                )
            )
            if len(links) > 200:
                raise SkillAccessError("skill_source_link_budget_exceeded")
            if not links:
                raise SkillAccessError("skill_source_missing")
            for link in links:
                artifact = await session.get(ArtifactRecord, link.trace_artifact_id)
                if (
                    artifact is None
                    or artifact.attributes.get("erased")
                    or artifact.redaction_status == "quarantined"
                    or artifact.content_hash != link.source_trace_hash
                    or artifact.run_id != link.source_run_id
                ):
                    raise SkillAccessError("skill_source_unavailable")
                run = await session.get(RunRecord, link.source_run_id)
                task = await session.get(TaskRecord, run.task_id) if run else None
                chat = await session.get(SessionRecord, task.session_id) if task else None
                if chat is None or chat.workspace_id != workspace_id:
                    raise SkillAccessError("skill_source_scope_invalid")
                if task.project_id is not None:
                    project = await session.get(ProjectRecord, task.project_id)
                    if (
                        project is None
                        or project.workspace_id != workspace_id
                        or str(project.status) != "available"
                        or project.authorization_version != task.project_authorization_version
                        or task.project_id != project_id
                    ):
                        raise SkillAccessError("skill_source_project_unavailable")
                try:
                    await check_run_references(session, run.id)
                except ValueError as error:
                    raise SkillAccessError("skill_source_reference_revoked") from error
                if link.source_kind == "personal":
                    source = await session.get(LearningSourceRecord, link.learning_source_id)
                    if (
                        source is None
                        or source.status != "valid"
                        or source.source_role != "personal"
                        or run.data_role != "personal"
                        or source.run_id != run.id
                        or source.artifact_id != artifact.id
                        or source.content_hash != artifact.content_hash
                    ):
                        raise SkillAccessError("skill_personal_source_revoked")
                    try:
                        pending.extend(
                            (UUID(value), ancestors | {current})
                            for value in source.parent_skill_versions
                        )
                    except (ValueError, TypeError) as error:
                        raise SkillAccessError("skill_parent_identity_invalid") from error
                elif link.source_kind == "train_eval":
                    evaluation = await session.get(EvalRunRecord, link.source_eval_run_id)
                    case = (
                        await session.get(EvalCaseRecord, evaluation.eval_case_id)
                        if evaluation
                        else None
                    )
                    if (
                        evaluation is None
                        or case is None
                        or str(case.split) != "train"
                        or not evaluation.passed
                        or evaluation.run_id != run.id
                    ):
                        raise SkillAccessError("skill_train_source_invalid")
                else:
                    raise SkillAccessError("skill_source_kind_invalid")
        return root
