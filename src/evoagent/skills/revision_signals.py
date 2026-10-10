"""Bounded revision suggestions from durable attributed evidence, without spending."""

from collections import defaultdict
from datetime import UTC, timedelta

from sqlalchemy import select

from evoagent.db.models import (
    LearningPolicyRecord,
    LearningRequestRecord,
    SkillObservationRecord,
    SkillVersionRecord,
)
from evoagent.learning.schema import LearningError
from evoagent.skills.access import SkillAccessError, SkillAccessPolicy
from evoagent.skills.usage_reports import SkillUsageReports
from evoagent.tasks.lease_guard import database_now


class SkillRevisionSignals:
    def __init__(self, factory):
        self.factory = factory

    async def suggest_revision(self, version_id, scope):
        async with self.factory() as session:
            version = await session.get(SkillVersionRecord, version_id)
            if version is None:
                raise LearningError("usage_version_missing")
            skill = await SkillUsageReports._scope(session, version.skill_id, scope)
            try:
                await SkillAccessPolicy().check(
                    session,
                    version_id,
                    workspace_id=scope.workspace_id,
                    project_id=scope.project_id,
                )
            except SkillAccessError:
                return None  # unavailable sources cannot support new learning
            now = await database_now(session)
            selected = SkillUsageReports._selected(skill.id, scope, now - timedelta(days=30))
            records = list(
                await session.scalars(
                    select(SkillObservationRecord)
                    .where(
                        SkillObservationRecord.version_id == version_id,
                        SkillObservationRecord.run_id.in_(selected),
                    )
                    .order_by(
                        SkillObservationRecord.first_finished_at.desc(), SkillObservationRecord.id
                    )
                    .limit(201)
                )
            )
            if len(records) > 200:
                return None  # bounded uncertainty never grants automatic consent
            steps = {item["id"] for item in version.definition["steps"]}
            latest = {}
            for row in records:
                prior = latest.get(row.run_id)
                if prior is None or row.feedback_revision > prior.feedback_revision:
                    latest[row.run_id] = row
            by_input = {}
            for row in sorted(
                latest.values(), key=lambda row: (row.first_finished_at, row.run_id.hex)
            ):
                if row.input_fingerprint:
                    by_input[row.input_fingerprint] = row
            grouped = defaultdict(list)
            for row in by_input.values():
                evidence = row.evidence
                if (
                    row.outcome != "verified_failure"
                    or row.attribution != "skill_related"
                    or evidence.get("verification_origin") not in ("user", "machine")
                    or not evidence.get("criterion_id")
                    or not evidence.get("evidence_refs")
                    or not evidence.get("associated_steps")
                    or not set(evidence["associated_steps"]) <= steps
                ):
                    continue
                # Same criterion and affected steps, independent inputs. A
                # provider outage or arbitrary free-text complaint is excluded.
                key = (evidence["criterion_id"], tuple(sorted(evidence["associated_steps"])))
                grouped[key].append(row)
            matches = [rows for rows in grouped.values() if len(rows) >= 3]
            if not matches:
                return None
            rows = max(
                matches, key=lambda rows: (len(rows), max(item.first_finished_at for item in rows))
            )
            policy = await session.get(LearningPolicyRecord, scope.workspace_id)
            cooldown = policy.cooldown_seconds if policy is not None else 86400
            existing = await session.scalar(
                select(LearningRequestRecord)
                .where(
                    LearningRequestRecord.workspace_id == scope.workspace_id,
                    LearningRequestRecord.project_id == scope.project_id,
                    LearningRequestRecord.target_skill_id == skill.id,
                    LearningRequestRecord.base_version_id == version_id,
                    LearningRequestRecord.request_kind == "propose",
                )
                .order_by(LearningRequestRecord.created_at.desc(), LearningRequestRecord.id)
                .limit(1)
            )
            if existing is not None:
                timestamp = (
                    existing.created_at
                    if existing.created_at.tzinfo
                    else existing.created_at.replace(tzinfo=UTC)
                )
                if existing.status not in (
                    "failed",
                    "rejected",
                    "cancelled",
                ) or timestamp >= now - timedelta(seconds=cooldown):
                    return None
            return {
                "version_id": version_id,
                "target_skill_id": skill.id,
                "reason": "repeated_attributed_failure",
                "independent_input_count": len(rows),
                "observation_ids": [row.id for row in rows],
                "origin_run_id": max(
                    rows, key=lambda row: (row.first_finished_at, row.run_id.hex)
                ).run_id,
                "requires_explicit_submission": True,
                "automatically_queued": False,
            }
