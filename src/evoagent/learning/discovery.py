"""Opt-in, bounded candidate discovery; never activate or judge a Skill."""

import asyncio
from contextlib import suppress
from datetime import timedelta

from sqlalchemy import exists, func, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from evoagent.db.models import (
    LearningPolicyRecord,
    LearningRequestRecord,
    RunFeedbackRecord,
    RunRecord,
    SessionRecord,
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
    def __init__(self, learning):
        self.learning = learning
        self.cursor = None  # cyclic query cursor, not a finished_at watermark

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
            await asyncio.sleep(60)
