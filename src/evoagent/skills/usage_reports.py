"""Read-only scope-bound usage projections; never infer causal improvement."""

from datetime import timedelta

from sqlalchemy import func, select

from evoagent.db.models import (
    RunRecord,
    RunSkillSelectionRecord,
    SessionRecord,
    SkillObservationRecord,
    SkillRecord,
    SkillVersionRecord,
    SpendRecord,
    TaskRecord,
    ToolCallRecord,
)
from evoagent.learning.schema import LearningError
from evoagent.tasks.lease_guard import database_now


class SkillUsageReports:
    def __init__(self, factory):
        self.factory = factory

    @staticmethod
    async def _scope(session, skill_id, scope):
        skill = await session.get(SkillRecord, skill_id)
        if (
            skill is None
            or skill.workspace_id != scope.workspace_id
            or skill.project_id not in (None, scope.project_id)
        ):
            raise LearningError("usage_scope_invalid")
        return skill

    @staticmethod
    def _selected(skill_id, scope, since):
        query = (
            select(RunSkillSelectionRecord.run_id)
            .join(
                SkillVersionRecord,
                SkillVersionRecord.id == RunSkillSelectionRecord.skill_version_id,
            )
            .join(RunRecord, RunRecord.id == RunSkillSelectionRecord.run_id)
            .join(TaskRecord, TaskRecord.id == RunRecord.task_id)
            .join(SessionRecord, SessionRecord.id == TaskRecord.session_id)
            .where(
                SkillVersionRecord.skill_id == skill_id,
                SessionRecord.workspace_id == scope.workspace_id,
                RunRecord.data_role == "personal",
                RunRecord.created_at >= since,
                RunSkillSelectionRecord.selection_policy_version == "skill-selector-v1",
            )
        )

        # Workspace-wide methods are selected in project tasks too. A supplied
        # project narrows statistics; None denotes the whole workspace here.
        if scope.project_id is not None:
            query = query.where(TaskRecord.project_id == scope.project_id)
        return query

    async def summarize(self, skill_id, scope, since=None):
        if since is not None and (since.tzinfo is None or since.utcoffset() is None):
            raise LearningError("usage_since_timezone_required")
        async with self.factory() as session:
            await self._scope(session, skill_id, scope)
            now = await database_now(session)
            since = since or now - timedelta(days=30)
            if since > now:
                raise LearningError("usage_since_in_future")
            selected = self._selected(skill_id, scope, since).distinct()
            selected_count = await session.scalar(
                select(func.count()).select_from(selected.subquery())
            )
            latest = (
                select(
                    SkillObservationRecord.run_id,
                    func.max(SkillObservationRecord.feedback_revision).label("revision"),
                )
                .where(SkillObservationRecord.run_id.in_(selected))
                .group_by(SkillObservationRecord.run_id)
                .subquery()
            )
            observations = (
                select(
                    SkillObservationRecord.outcome, SkillObservationRecord.attribution, func.count()
                )
                .join(
                    latest,
                    (latest.c.run_id == SkillObservationRecord.run_id)
                    & (latest.c.revision == SkillObservationRecord.feedback_revision),
                )
                .join(
                    SkillVersionRecord, SkillVersionRecord.id == SkillObservationRecord.version_id
                )
                .where(SkillVersionRecord.skill_id == skill_id)
                .group_by(SkillObservationRecord.outcome, SkillObservationRecord.attribution)
            )
            outcomes = {"verified_success": 0, "verified_failure": 0, "unknown": 0}
            related_failures = 0
            for outcome, attribution, count in await session.execute(observations):
                outcomes[outcome] += count
                if outcome == "verified_failure" and attribution == "skill_related":
                    related_failures += count
            projected = sum(outcomes.values())
            tools = await session.scalar(
                select(func.count())
                .select_from(ToolCallRecord)
                .where(ToolCallRecord.run_id.in_(selected))
            )
            tokens = (
                await session.execute(
                    select(
                        func.coalesce(func.sum(SpendRecord.input_tokens), 0),
                        func.coalesce(func.sum(SpendRecord.output_tokens), 0),
                        func.count(),
                    ).where(SpendRecord.run_id.in_(selected))
                )
            ).one()
            durations = list(
                await session.execute(
                    select(RunRecord.started_at, RunRecord.ended_at)
                    .where(RunRecord.id.in_(selected))
                    .order_by(RunRecord.created_at.desc(), RunRecord.id)
                    .limit(1001)
                )
            )
            elapsed = [
                (end - start).total_seconds()
                for start, end in durations[:1000]
                if start is not None and end is not None and end >= start
            ]
            return {
                "skill_id": skill_id,
                "workspace_id": scope.workspace_id,
                "project_id": scope.project_id,
                "since": since,
                "selected_count": selected_count,
                "observation_coverage": "trial_only",
                "projected_count": projected,
                "unprojected_count": selected_count - projected,
                "outcomes": outcomes,
                "skill_related_failures": related_failures,
                "tool_call_count": tools,
                "accounted_input_tokens": tokens[0],
                "accounted_output_tokens": tokens[1],
                "spend_record_count": tokens[2],
                "token_coverage": "settled_spend_records_only",
                "elapsed_sample_count": len(elapsed),
                "elapsed_sample_truncated": len(durations) > 1000,
                "elapsed_mean_seconds": sum(elapsed) / len(elapsed) if elapsed else None,
                "causal_benefit_established": False,
            }

    async def list_evidence(self, version_id, scope, *, cursor=None, limit=50):
        if type(limit) is not int or not 1 <= limit <= 100:
            raise LearningError("usage_evidence_limit_invalid")
        async with self.factory() as session:
            version = await session.get(SkillVersionRecord, version_id)
            if version is None:
                raise LearningError("usage_version_missing")
            await self._scope(session, version.skill_id, scope)
            query = (
                select(SkillObservationRecord)
                .join(RunRecord, RunRecord.id == SkillObservationRecord.run_id)
                .join(TaskRecord, TaskRecord.id == RunRecord.task_id)
                .join(SessionRecord, SessionRecord.id == TaskRecord.session_id)
                .where(
                    SkillObservationRecord.version_id == version_id,
                    SessionRecord.workspace_id == scope.workspace_id,
                    RunRecord.data_role == "personal",
                )
            )
            if scope.project_id is not None:
                query = query.where(TaskRecord.project_id == scope.project_id)
            if cursor is not None:
                query = query.where(SkillObservationRecord.id > cursor)
            rows = list(
                await session.scalars(query.order_by(SkillObservationRecord.id).limit(limit + 1))
            )
            # History includes superseded revisions; summaries use latest only.
            # Do not return arbitrary evidence JSON, comments, source or artifact bodies.
            return {
                "items": [
                    {
                        "id": row.id,
                        "run_id": row.run_id,
                        "version_id": row.version_id,
                        "trial_id": row.trial_id,
                        "feedback_revision": row.feedback_revision,
                        "outcome": row.outcome,
                        "attribution": row.attribution,
                        "first_finished_at": row.first_finished_at,
                        "verification_origin": row.evidence.get("verification_origin")
                        if row.evidence.get("verification_origin") in ("user", "machine", "unknown")
                        else "unknown",
                    }
                    for row in rows[:limit]
                ],
                "next_cursor": rows[limit - 1].id if len(rows) > limit else None,
            }
