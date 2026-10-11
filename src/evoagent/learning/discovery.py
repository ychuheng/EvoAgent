"""Opt-in, bounded candidate discovery; never activate or judge a Skill."""

import asyncio
from contextlib import suppress
from datetime import timedelta

from sqlalchemy import exists, func, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from evoagent.db.models import (
    LearningPolicyRecord,
    LearningRequestAliasRecord,
    LearningRequestRecord,
    LearningSourceRecord,
    RunFeedbackRecord,
    RunRecord,
    SessionRecord,
    SkillObservationRecord,
    SkillRecord,
    SkillVersionRecord,
    TaskRecord,
)
from evoagent.learning.schema import LearningError, LearningSubmission
from evoagent.learning.sources import PersonalSourceService
from evoagent.skills.canonical import content_hash
from evoagent.tasks.lease_guard import database_now


async def check_discovery_limits(
    session, workspace_id, task, policy, fingerprint, *, join_history=False
):
    # Caller already owns Run -> Workspace locks; checks and request insertion
    # share that short transaction, including cross-worker daily limits.
    if policy["mode"] != "suggest" or not fingerprint:
        raise LearningError("learning_discovery_not_authorized")
    now = await database_now(session)
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    base = select(LearningRequestRecord).where(
        LearningRequestRecord.workspace_id == workspace_id,
        LearningRequestRecord.trigger == "discover",
        LearningRequestRecord.request_kind == "propose",
    )
    daily = await session.scalar(
        select(func.count()).select_from(
            base.where(LearningRequestRecord.created_at >= midnight).subquery()
        )
    )
    if daily >= policy["daily_candidate_limit"]:
        raise LearningError("learning_discovery_daily_limit")
    # Exact fingerprints are deduplication, lexical similarity is not authority
    # to overwrite, merge or delete existing versions.
    ordered = base.order_by(
        LearningRequestRecord.created_at.desc(), LearningRequestRecord.id
    ).limit(1001)
    if join_history:
        # Metadata only, in the same admission transaction. Never cache authority
        # across transactions or load each historical Run/Task separately.
        previous = list(
            (
                await session.execute(
                    ordered.with_only_columns(
                        LearningRequestRecord.policy_snapshot,
                        LearningRequestRecord.created_at,
                        TaskRecord.family,
                        TaskRecord.project_id,
                    )
                    .join(RunRecord, LearningRequestRecord.origin_run_id == RunRecord.id)
                    .join(TaskRecord, RunRecord.task_id == TaskRecord.id)
                )
            ).all()
        )
    else:
        previous = list(await session.scalars(ordered))
    if len(previous) > 1000:
        raise LearningError("learning_discovery_history_bound")
    for row in previous:
        snapshot, timestamp = (
            (row[0], row[1]) if join_history else (row.policy_snapshot, row.created_at)
        )
        if snapshot.get("discovery_fingerprint") == fingerprint:
            raise LearningError("learning_discovery_duplicate")
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=now.tzinfo)
        if timestamp >= now - timedelta(seconds=policy["cooldown_seconds"]):
            if join_history:
                family, project_id = row[2], row[3]
            else:
                original = await session.get(
                    TaskRecord, (await session.get(RunRecord, row.origin_run_id)).task_id
                )
                family, project_id = original.family, original.project_id
            if family == task.family and project_id == task.project_id:
                raise LearningError("learning_discovery_cooldown")


class CandidateDiscoveryService:
    def __init__(self, learning, *, store=None, settings=None):
        self.learning = learning
        self.store, self.settings = store, settings
        self.cursor = None  # cyclic query cursor, not a finished_at watermark
        self.revision_cursor = None

    async def discover_revisions(self, *, limit=50):
        """Only aggregate already-frozen, individually consented experiences."""
        if not self.learning.enabled or self.store is None or self.settings is None:
            return 0
        if type(limit) is not int or not 1 <= limit <= 50:
            raise LearningError("invalid_discovery_limit")
        from evoagent.skills.revision_signals import SkillRevisionSignals
        from evoagent.skills.trials import TrialScope

        query = (
            select(SkillObservationRecord.id)
            .join(RunRecord, RunRecord.id == SkillObservationRecord.run_id)
            .join(TaskRecord, TaskRecord.id == RunRecord.task_id)
            .join(SessionRecord, SessionRecord.id == TaskRecord.session_id)
            .join(
                LearningPolicyRecord,
                LearningPolicyRecord.workspace_id == SessionRecord.workspace_id,
            )
            .where(
                LearningPolicyRecord.mode == "suggest",
                SkillObservationRecord.outcome == "verified_failure",
                RunRecord.data_role == "personal",
            )
            .order_by(SkillObservationRecord.id)
        )
        async with self.learning.factory() as session:
            ids = list(
                await session.scalars(
                    (
                        query.where(SkillObservationRecord.id > self.revision_cursor)
                        if self.revision_cursor
                        else query
                    ).limit(limit)
                )
            )
        self.revision_cursor = ids[-1] if ids else None
        created, visited = 0, set()
        for observation_id in ids:
            try:
                async with self.learning.factory() as session:
                    obs = await session.get(SkillObservationRecord, observation_id)
                    _, task, chat = await self.learning._scope(session, obs.run_id)
                    key = (obs.version_id, chat.workspace_id, task.project_id)
                if key in visited:
                    continue
                visited.add(key)
                scope = TrialScope(key[1], key[2])
                signal = await SkillRevisionSignals(self.learning.factory).suggest_revision(
                    key[0], scope, exact_project=True
                )
                if signal and await self._append_revision(signal, scope):
                    created += 1
            except (LearningError, IntegrityError):
                continue
        return created

    async def _append_revision(self, signal, scope):
        from sqlalchemy import update

        from evoagent.learning.revision_aggregation import AggregationSourceRef
        from evoagent.memory.schema import MemoryError
        from evoagent.skills.provenance import formal_input_fingerprint
        from evoagent.skills.source_verification import SkillSourceVerifier
        from evoagent.tools.base import ToolError

        refs = []
        async with self.learning.factory() as session:
            skill = await session.get(SkillRecord, signal["target_skill_id"])
            base = await session.get(SkillVersionRecord, signal["version_id"])
            policy = await self.learning._policy(session, scope.workspace_id)
            configuration = self.learning.generator_configuration or {}
            if policy["mode"] != "suggest" or (
                configuration.get("provider") not in (None, "mock")
                and any(
                    policy[k] is None or policy[k] <= 0
                    for k in ("daily_limit_micros", "request_limit_micros")
                )
            ):
                return False
            # Cap to ten independent inputs, always include the newest origin.
            observations = list(
                await session.scalars(
                    select(SkillObservationRecord)
                    .where(SkillObservationRecord.id.in_(signal["observation_ids"]))
                    .order_by(
                        SkillObservationRecord.first_finished_at.desc(),
                        SkillObservationRecord.run_id.desc(),
                    )
                    .limit(10)
                )
            )
            for obs in observations:
                _, task, chat = await self.learning._scope(session, obs.run_id)
                if chat.workspace_id != scope.workspace_id or task.project_id != scope.project_id:
                    continue
                feedback = await session.scalar(
                    select(RunFeedbackRecord)
                    .where(RunFeedbackRecord.run_id == obs.run_id)
                    .order_by(RunFeedbackRecord.revision.desc())
                    .limit(1)
                )
                if (
                    feedback is None
                    or not feedback.learn_from_feedback
                    or feedback.intent != "method"
                    or feedback.verdict not in {"needs_fix", "incorrect"}
                    or feedback.revision != obs.feedback_revision
                ):
                    continue
                source = await session.scalar(
                    select(LearningSourceRecord)
                    .where(
                        LearningSourceRecord.run_id == obs.run_id,
                        LearningSourceRecord.feedback_id == feedback.id,
                        LearningSourceRecord.status == "valid",
                    )
                    .order_by(LearningSourceRecord.created_at.desc())
                    .limit(1)
                )
                if source is None:
                    continue  # never retroactively register unconsented history
                evidence = await PersonalSourceService(
                    self.learning.factory
                ).build_evidence_in_session(session, obs.run_id, feedback.id)
                independent = formal_input_fingerprint(evidence)
                if independent is None or any(
                    r.independent_input_hash == independent for r in refs
                ):
                    continue
                refs.append(
                    AggregationSourceRef(
                        source_id=source.id,
                        run_id=obs.run_id,
                        artifact_id=source.artifact_id,
                        content_hash=source.content_hash,
                        source_revision=source.source_revision,
                        revocation_epoch=source.revocation_epoch,
                        feedback_id=feedback.id,
                        observation_id=obs.id,
                        input_fingerprint=obs.input_fingerprint,
                        independent_input_hash=independent,
                    )
                )
            contract = {
                "contract": "revision-aggregation:v1",
                "target_lock_version": skill.lock_version,
                "base_content_hash": base.content_hash,
                "criterion_id": signal["criterion_id"],
                "associated_steps": signal["associated_steps"],
                "sources": [ref.model_dump(mode="json") for ref in refs],
            }
        if len(refs) < 3 or not any(r.run_id == signal["origin_run_id"] for r in refs):
            return False
        try:
            async with asyncio.timeout(10):
                await SkillSourceVerifier(
                    self.learning.factory, self.settings, scope, store=self.store
                ).verify(base.id)
                sources = PersonalSourceService(self.learning.factory, artifact_store=self.store)
                for ref in refs:
                    await sources.read_frozen(
                        ref.source_id, expected_revocation_epoch=ref.revocation_epoch
                    )
        except (MemoryError, ValueError, OSError, TimeoutError, ToolError):
            return False
        async with self.learning.factory() as session:
            for run_id in sorted({ref.run_id for ref in refs}, key=str):
                await session.execute(
                    update(RunRecord)
                    .where(RunRecord.id == run_id)
                    .values(next_feedback_revision=RunRecord.next_feedback_revision)
                )
            # _append_proposal acquires Workspace next and rechecks policy,
            # quotas and every frozen source in this same admission transaction.
            fingerprint = content_hash(
                {"revision_aggregation": contract, "version_id": str(base.id)}
            )
            primary = next(ref for ref in refs if ref.run_id == signal["origin_run_id"])
            client_key = "aggregate:v1:" + fingerprint[7:]
            if await session.get(LearningRequestAliasRecord, (scope.workspace_id, client_key)):
                return False  # replay is not a newly admitted candidate
            await self.learning._append_proposal(
                session,
                primary.run_id,
                LearningSubmission(
                    client_request_id=client_key,
                    feedback_id=primary.feedback_id,
                    target_skill_id=skill.id,
                    expected_base_version_id=base.id,
                ),
                trigger="discover",
                discovery_fingerprint=fingerprint,
                revision_aggregation=contract,
            )
            await session.commit()
        return True

    async def discover_candidates(self, *, limit=50):
        if not self.learning.enabled:
            return 0
        if type(limit) is not int or not 1 <= limit <= 50:
            raise LearningError("invalid_discovery_limit")
        query = (
            select(RunRecord.id)
            .join(TaskRecord, RunRecord.task_id == TaskRecord.id)
            .join(SessionRecord, TaskRecord.session_id == SessionRecord.id)
            .join(
                LearningPolicyRecord,
                LearningPolicyRecord.workspace_id == SessionRecord.workspace_id,
            )
            .where(
                LearningPolicyRecord.mode == "suggest",
                RunRecord.data_role == "personal",
                RunRecord.status == "completed",
                ~exists(
                    select(LearningRequestRecord.id).where(
                        LearningRequestRecord.origin_run_id == RunRecord.id,
                        LearningRequestRecord.request_kind == "propose",
                    )
                ),
            )
            .order_by(RunRecord.id)
        )
        async with self.learning.factory() as session:
            ids = list(
                await session.scalars(
                    (query.where(RunRecord.id > self.cursor) if self.cursor else query).limit(limit)
                )
            )
        if not ids:
            self.cursor = None
            return 0
        self.cursor = ids[-1]
        created = 0
        for run_id in ids:
            try:
                async with self.learning.factory() as session:
                    # Admission repeats all source and project checks under
                    # the established Run -> Workspace lock order.
                    await PersonalSourceService(self.learning.factory)._lock_run_scope(
                        session, run_id
                    )
                    run, task, chat = await self.learning._scope(session, run_id)
                    policy = await self.learning._policy(session, chat.workspace_id)
                    if policy["mode"] != "suggest":
                        continue
                    if await session.scalar(
                        select(RunFeedbackRecord.id)
                        .where(RunFeedbackRecord.run_id == run_id)
                        .limit(1)
                    ):
                        # Explicit feedback owns routing. Never turn a fact,
                        # uncertainty or a refusal into automatic method consent.
                        continue
                    if await session.scalar(
                        select(LearningRequestRecord.id)
                        .where(
                            LearningRequestRecord.origin_run_id == run_id,
                            LearningRequestRecord.request_kind == "propose",
                        )
                        .limit(1)
                    ):
                        continue
                    evidence = await PersonalSourceService(
                        self.learning.factory, max_source_risk=policy["max_source_risk"]
                    ).build_evidence_in_session(session, run_id, None)
                    # A completed status or model-written final answer is
                    # insufficient. Only persisted acceptance evidence qualifies.
                    if not evidence.verified_facts or not all(
                        item.get("result", {}).get("passed") is True
                        for item in evidence.verified_facts
                    ):
                        continue
                    configuration = self.learning.generator_configuration or {}
                    if configuration.get("provider") not in (None, "mock") and any(
                        policy[key] is None or policy[key] <= 0
                        for key in ("daily_limit_micros", "request_limit_micros")
                    ):
                        continue
                    fingerprint = content_hash(
                        {
                            "family": task.family,
                            "project_id": str(task.project_id),
                            "goal": evidence.goal,
                            "inputs": evidence.input_refs,
                            "versions": evidence.selected_versions,
                        }
                    )
                    await self.learning._append_proposal(
                        session,
                        run_id,
                        LearningSubmission(client_request_id=f"discover:v1:{run_id}"),
                        trigger="discover",
                        discovery_fingerprint=fingerprint,
                    )
                    await session.commit()
                    created += 1
            except (LearningError, IntegrityError):
                continue  # no model or task execution occurs in this scanner
        return created

    async def periodic_discovery(self):
        while True:
            # Next cyclic pass retries DB errors; terminal tasks are unchanged.
            with suppress(SQLAlchemyError):
                await self.discover_candidates()
                await self.discover_revisions()
            await asyncio.sleep(60)
