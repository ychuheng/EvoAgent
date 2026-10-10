"""Shared permission checks for immutable skill bodies and their source graph."""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select

from evoagent.db.models import (
    ArtifactRecord,
    EvalCaseRecord,
    EvalDatasetRecord,
    EvalExperimentRecord,
    EvalRunRecord,
    LearningRequestRecord,
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
from evoagent.skills.lifecycle import SkillStatus, SkillVersionStatus
from evoagent.skills.schema import SkillDefinition


class SkillAccessError(ValueError):
    """Stable error with no source body in its message."""


@dataclass(frozen=True)
class SkillSourceProof:
    artifact_id: UUID
    run_id: UUID
    content_hash: str
    uri: str
    size_bytes: int
    source_kind: str
    learning_source_id: UUID | None
    revocation_epoch: int | None


def source_graph_identity(proofs):
    """Safe immutable evidence metadata; never source bodies or storage URIs."""
    return [
        {
            "artifact_id": str(proof.artifact_id),
            "run_id": str(proof.run_id),
            "content_hash": proof.content_hash,
            "size_bytes": proof.size_bytes,
            "source_kind": proof.source_kind,
            "learning_source_id": str(proof.learning_source_id)
            if proof.learning_source_id
            else None,
            "revocation_epoch": proof.revocation_epoch,
        }
        for proof in sorted(proofs.values(), key=lambda item: str(item.artifact_id))
    ]


class SkillAccessPolicy:
    async def check(
        self, session, version_id, *, workspace_id, project_id=None, source_proofs=None
    ):
        pending, seen = [(version_id, frozenset(), frozenset())], set()
        root = None
        while pending:
            current, ancestors, supersession_path = pending.pop()
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
            # A retired pointer is compatible with a frozen lineage; explicit
            # disabling/rejection is revocation and applies to every ancestor.
            superseded_ancestor = (
                bool(ancestors)
                and skill.status is SkillStatus.DEPRECATED
                and skill.superseded_by_skill_id in supersession_path
            )
            if (skill.status is not SkillStatus.ENABLED and not superseded_ancestor) or (
                version.lifecycle_status is SkillVersionStatus.REJECTED
            ):
                raise SkillAccessError("skill_source_version_revoked")
            try:
                definition = SkillDefinition.model_validate(version.definition)
            except ValueError:
                raise SkillAccessError("skill_definition_invalid") from None
            if (
                definition.schema_version not in {1, 2}
                or version.schema_version != definition.schema_version
            ):
                raise SkillAccessError("skill_schema_version_invalid")
            if detect_sensitive(canonical_json(definition.model_dump(mode="json"))):
                raise SkillAccessError("skill_sensitive_content")
            if root is None:
                root = version
            if version.extraction_key.startswith("merge:"):
                # Only host-created merge roots use this namespace; ordinary
                # versions incur no additional request lookup.
                from evoagent.learning.repository import LearningRepository
                from evoagent.skills.merging import MergeParent, merge_extraction_key

                requests = list(
                    await session.scalars(
                        select(LearningRequestRecord)
                        .where(
                            LearningRequestRecord.candidate_version_id == version.id,
                            LearningRequestRecord.request_kind == "propose",
                            LearningRequestRecord.trigger == "merge",
                        )
                        .limit(2)
                    )
                )
                if len(requests) != 1:
                    raise SkillAccessError("skill_merge_lineage_missing")
                request = requests[0]
                if (
                    request.status not in {"ready_for_review", "completed"}
                    or request.workspace_id != skill.workspace_id
                    or request.project_id != skill.project_id
                    or request.policy_snapshot.get("merge_contract") != "merge:v1"
                    or content_hash(request.policy_snapshot) != request.policy_hash
                    or request.frozen_inputs.get("policy_hash") != request.policy_hash
                    or request.source_key
                    != LearningRepository.build_source_key("propose", request.frozen_inputs)
                    or request.policy_snapshot.get("definition_hash") != version.content_hash
                    or version.extraction_key
                    != merge_extraction_key(request.source_key, version.content_hash)
                ):
                    raise SkillAccessError("skill_merge_lineage_invalid")
                try:
                    parents = [
                        MergeParent.model_validate(item)
                        for item in request.policy_snapshot["lineage"]
                    ]
                    if not 2 <= len(parents) <= 4 or len({p.skill_id for p in parents}) != len(
                        parents
                    ):
                        raise ValueError("invalid lineage cardinality")
                    for parent_ref in parents:
                        parent = await session.get(SkillVersionRecord, parent_ref.version_id)
                        if (
                            parent is None
                            or parent.skill_id != parent_ref.skill_id
                            or parent.content_hash != parent_ref.content_hash
                        ):
                            raise ValueError("invalid lineage identity")
                        pending.append(
                            (parent.id, ancestors | {current}, supersession_path | {skill.id})
                        )
                except (ValueError, TypeError, KeyError) as error:
                    raise SkillAccessError("skill_merge_lineage_invalid") from error
                supersession_path = supersession_path | {skill.id}
            if version.parent_version_id is not None:
                parent = await session.get(SkillVersionRecord, version.parent_version_id)
                if parent is None or parent.skill_id != version.skill_id:
                    raise SkillAccessError("skill_revision_parent_invalid")
                pending.append((parent.id, ancestors | {current}, supersession_path))
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
                source = None
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
                            (UUID(value), ancestors | {current}, supersession_path)
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
                    experiment = (
                        await session.get(EvalExperimentRecord, evaluation.experiment_id)
                        if evaluation
                        else None
                    )
                    dataset = (
                        await session.get(EvalDatasetRecord, case.dataset_id) if case else None
                    )
                    if (
                        experiment is None
                        or experiment.purpose != "formal"
                        or dataset is None
                        or dataset.purpose != "formal"
                        or evaluation is None
                        or case is None
                        or str(case.split) != "train"
                        or not evaluation.passed
                        or evaluation.run_id != run.id
                    ):
                        raise SkillAccessError("skill_train_source_invalid")
                else:
                    raise SkillAccessError("skill_source_kind_invalid")
                if source_proofs is not None:
                    source_proofs[artifact.id] = SkillSourceProof(
                        artifact.id,
                        run.id,
                        artifact.content_hash,
                        artifact.uri,
                        artifact.size_bytes,
                        link.source_kind,
                        source.id if source else None,
                        source.revocation_epoch if source else None,
                    )
        return root
