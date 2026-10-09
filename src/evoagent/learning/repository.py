"""反馈的唯一构造入口；调用者负责与学习请求在同一事务提交。"""

from uuid import UUID

from sqlalchemy import select, update

from evoagent.db.models import (
    LearningRequestAliasRecord,
    LearningRequestRecord,
    RunFeedbackRecord,
    RunRecord,
    SessionRecord,
    SkillRecord,
    SkillVersionRecord,
    TaskRecord,
    WorkspaceRecord,
)
from evoagent.db.repositories.base import ConcurrentUpdateError, RecordNotFoundError
from evoagent.db.repositories.runs import RunRepository
from evoagent.learning.schema import FeedbackPayload
from evoagent.privacy.redaction import detect_sensitive
from evoagent.skills.canonical import canonical_json, content_hash

_IDENTITY_FIELDS = {
    "propose": (
        "run_id",
        "learning_revision",
        "learning_payload_hash",
        "source_revision",
        "target_skill_id",
        "base_version_id",
        "policy_hash",
    ),
    "validate": (
        "parent_request_id",
        "candidate_version_id",
        "candidate_content_hash",
        "validation_input_manifest_hash",
        "validation_criteria_hash",
        "validation_policy_hash",
        "validator_version",
        "target_scope_key",
    ),
}


class LearningRepository:
    def __init__(self, session):
        self.session = session

    @staticmethod
    def build_source_key(kind: str, frozen_inputs: dict) -> str:
        if kind not in _IDENTITY_FIELDS:
            raise ValueError("unsupported learning request kind")
        fields = _IDENTITY_FIELDS[kind]
        missing = set(fields) - frozen_inputs.keys()
        if missing:
            raise ValueError(f"missing frozen identity fields: {sorted(missing)}")
        identity = {key: frozen_inputs[key] for key in fields}
        return f"{kind}:v1:{content_hash(identity)}"

    async def append_request(
        self,
        *,
        workspace_id: UUID,
        origin_run_id: UUID,
        client_request_id: str,
        kind: str,
        frozen_inputs: dict,
        policy_snapshot: dict,
        request_body: dict,
        trigger: str = "manual",
        project_id: UUID | None = None,
        parent_request_id: UUID | None = None,
        target_skill_id: UUID | None = None,
        base_version_id: UUID | None = None,
    ) -> LearningRequestRecord:
        if not client_request_id or len(client_request_id) > 128:
            raise ValueError("client_request_id must have 1..128 characters")
        if detect_sensitive(canonical_json(request_body)):
            raise ValueError("sensitive_learning_request")
        body_hash = content_hash(request_body)
        policy_hash = content_hash(policy_snapshot)
        policy_field = "policy_hash" if kind == "propose" else "validation_policy_hash"
        if frozen_inputs.get(policy_field) != policy_hash:
            raise ValueError("frozen policy identity does not match policy snapshot")
        key = self.build_source_key(kind, frozen_inputs)
        # 工作区锁使语义去重和客户端别名注册成为同一个跨进程事务。
        locked = await self.session.scalar(
            update(WorkspaceRecord)
            .where(WorkspaceRecord.id == workspace_id)
            .values(name=WorkspaceRecord.name)
            .returning(WorkspaceRecord.id)
        )
        if locked is None:
            raise RecordNotFoundError("workspace does not exist")
        origin_workspace = await self.session.scalar(
            select(SessionRecord.workspace_id)
            .join(TaskRecord, TaskRecord.session_id == SessionRecord.id)
            .join(RunRecord, RunRecord.task_id == TaskRecord.id)
            .where(RunRecord.id == origin_run_id)
        )
        if origin_workspace != workspace_id:
            raise ValueError("learning origin workspace mismatch")
        if kind == "propose":
            expected = {
                "run_id": origin_run_id,
                "target_skill_id": target_skill_id,
                "base_version_id": base_version_id,
            }
            for field, value in expected.items():
                if frozen_inputs[field] != (str(value) if value is not None else None):
                    raise ValueError("frozen request identity mismatch")
        if target_skill_id is not None:
            skill = await self.session.get(SkillRecord, target_skill_id)
            if skill is None or skill.workspace_id != workspace_id:
                raise ValueError("target skill workspace mismatch")
            if skill.project_id is not None and skill.project_id != project_id:
                raise ValueError("target skill project mismatch")
        if base_version_id is not None:
            base = await self.session.get(SkillVersionRecord, base_version_id)
            if base is None or base.skill_id != target_skill_id:
                raise ValueError("base version target mismatch")
        if kind == "validate":
            parent = await self.session.get(LearningRequestRecord, parent_request_id)
            if (
                parent is None
                or parent.workspace_id != workspace_id
                or parent.request_kind != "propose"
            ):
                raise ValueError("validation parent mismatch")
            if frozen_inputs["parent_request_id"] != str(parent.id):
                raise ValueError("validation frozen parent mismatch")
            candidate = await self.session.get(SkillVersionRecord, parent.candidate_version_id)
            if (
                candidate is None
                or frozen_inputs["candidate_version_id"] != str(candidate.id)
                or frozen_inputs["candidate_content_hash"] != candidate.content_hash
            ):
                raise ValueError("validation candidate mismatch")
        alias = await self.session.get(
            LearningRequestAliasRecord, (workspace_id, client_request_id)
        )
        if alias is not None:
            existing = await self.session.get(LearningRequestRecord, alias.request_id)
            if alias.request_body_hash != body_hash or existing.source_key != key:
                raise ConcurrentUpdateError("learning_request_conflict: identity changed")
            return existing
        existing = await self.session.scalar(
            select(LearningRequestRecord).where(
                LearningRequestRecord.workspace_id == workspace_id,
                LearningRequestRecord.source_key == key,
            )
        )
        if existing is None:
            existing = LearningRequestRecord(
                request_kind=kind,
                workspace_id=workspace_id,
                origin_run_id=origin_run_id,
                client_request_id=client_request_id,
                request_body_hash=body_hash,
                source_key=key,
                trigger=trigger,
                project_id=project_id,
                parent_request_id=parent_request_id,
                target_skill_id=target_skill_id,
                base_version_id=base_version_id,
                policy_snapshot=policy_snapshot,
                policy_hash=policy_hash,
                frozen_inputs=frozen_inputs,
                feedback_revision=int(frozen_inputs.get("learning_revision", 0)),
            )
            self.session.add(existing)
            await self.session.flush()
        self.session.add(
            LearningRequestAliasRecord(
                workspace_id=workspace_id,
                client_request_id=client_request_id,
                request_id=existing.id,
                request_body_hash=body_hash,
            )
        )
        await self.session.flush()
        return existing

    async def append_feedback(
        self,
        run_id: UUID,
        client_request_id: str,
        payload: FeedbackPayload,
        actor_id: str,
        expected_revision: int | None = None,
    ) -> RunFeedbackRecord:
        if not client_request_id or len(client_request_id) > 128:
            raise ValueError("client_request_id must have 1..128 characters")
        if not actor_id or len(actor_id) > 128:
            raise ValueError("actor_id must have 1..128 characters")
        body = payload.model_dump(mode="json")
        if detect_sensitive(canonical_json(body)):
            raise ValueError("sensitive_feedback_content")
        # 引用顺序与重复引用不构成学习语义变化；注释只进入完整请求哈希。
        references = sorted({canonical_json(ref) for ref in body["evidence_refs"]})
        import json

        body["evidence_refs"] = [json.loads(ref) for ref in references]
        body_hash = content_hash(body)
        semantics = {key: body[key] for key in ("intent", "verdict", "correction", "evidence_refs")}
        learning_hash = content_hash(semantics)
        # UPDATE 获得聚合写锁，SQLite 与 PostgreSQL 都可串行化该临界区。
        locked = await self.session.scalar(
            update(RunRecord)
            .where(RunRecord.id == run_id)
            .values(next_feedback_revision=RunRecord.next_feedback_revision)
            .returning(RunRecord.id)
        )
        if locked is None:
            raise RecordNotFoundError(f"run does not exist: {run_id}")
        existing = await self.session.scalar(
            select(RunFeedbackRecord).where(
                RunFeedbackRecord.run_id == run_id,
                RunFeedbackRecord.client_request_id == client_request_id,
            )
        )
        if existing is not None:
            if existing.request_body_hash != body_hash:
                raise ConcurrentUpdateError("feedback_conflict: client request body changed")
            return existing
        latest = await self.session.scalar(
            select(RunFeedbackRecord)
            .where(RunFeedbackRecord.run_id == run_id)
            .order_by(RunFeedbackRecord.revision.desc())
            .limit(1)
        )
        if expected_revision is not None and expected_revision != (
            latest.revision if latest else 0
        ):
            raise ConcurrentUpdateError("feedback_conflict: revision changed")
        learning_revision = (
            latest.learning_revision + (latest.learning_payload_hash != learning_hash)
            if latest
            else 1
        )
        record = RunFeedbackRecord(
            run_id=run_id,
            client_request_id=client_request_id,
            revision=await RunRepository(self.session).allocate_feedback_revision(run_id),
            learning_revision=learning_revision,
            learning_payload_hash=learning_hash,
            request_body_hash=body_hash,
            intent=body["intent"],
            verdict=body["verdict"],
            comment=body["comment"],
            correction=body["correction"],
            evidence_refs=body["evidence_refs"],
            supersedes_id=latest.id if latest else None,
            actor_id=actor_id,
        )
        self.session.add(record)
        await self.session.flush()
        return record
