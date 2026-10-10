"""Bounded read-only housekeeping hints; no learning or lifecycle writes."""

from datetime import UTC, timedelta
from itertools import combinations

from sqlalchemy import func, select

from evoagent.db.models import SkillRecord, SkillVersionRecord
from evoagent.retrieval.lexical import tokenize
from evoagent.skills.access import SkillAccessError, SkillAccessPolicy
from evoagent.skills.lifecycle import SkillStatus
from evoagent.skills.usage_reports import SkillUsageReports
from evoagent.tasks.lease_guard import database_now


def method_terms(definition):
    # Names, DSL keys and tool names would inflate unrelated method similarity.
    text = " ".join(
        [definition.get("description", "")]
        + [step.get("instruction", "") for step in definition.get("steps", [])]
    )
    return frozenset(tokenize(text))


def similarity(left, right):
    a, b = method_terms(left), method_terms(right)
    if min(len(a), len(b)) < 5:
        return 0.0
    return len(a & b) / len(a | b)


class SkillLibrarySuggestions:
    def __init__(self, factory):
        self.factory = factory

    async def inspect(self, scope):
        async with self.factory() as session:
            now = await database_now(session)
            since = now - timedelta(days=30)
            latest = (
                select(func.max(SkillVersionRecord.version))
                .where(SkillVersionRecord.skill_id == SkillRecord.id)
                .correlate(SkillRecord)
                .scalar_subquery()
            )
            query = (
                select(SkillRecord, SkillVersionRecord)
                .join(
                    SkillVersionRecord,
                    (SkillVersionRecord.skill_id == SkillRecord.id)
                    & (SkillVersionRecord.version == latest),
                )
                .where(
                    SkillRecord.workspace_id == scope.workspace_id,
                    SkillRecord.project_id == scope.project_id,
                    SkillRecord.status == SkillStatus.ENABLED,
                )
                .order_by(SkillRecord.id)
                .limit(21)
            )
            rows = list(await session.execute(query))
            valid, low_usage = [], []
            unavailable = 0
            for skill, version in rows[:20]:
                try:
                    await SkillAccessPolicy().check(
                        session,
                        version.id,
                        workspace_id=scope.workspace_id,
                        project_id=scope.project_id,
                    )
                except SkillAccessError:
                    unavailable += 1
                    continue
                valid.append((skill, version))
                created = skill.created_at
                created = created if created.tzinfo else created.replace(tzinfo=UTC)
                if created <= since:
                    selected = SkillUsageReports._selected(skill.id, scope, since)
                    used = await session.scalar(select(selected.exists()))
                    if not used:
                        low_usage.append(
                            {"skill_id": skill.id, "reason": "no_selection_in_30_days"}
                        )
            hints = []
            for (a, av), (b, bv) in combinations(valid, 2):
                # The merge operation requires exact scope; different declared
                # applicability is not evidence of a duplicate procedure.
                if av.definition.get("applicability") != bv.definition.get("applicability"):
                    continue
                score = similarity(av.definition, bv.definition)
                exact = av.content_hash == bv.content_hash
                if exact or score >= 0.65:
                    hints.append(
                        {
                            "parents": [
                                {
                                    "skill_id": s.id,
                                    "version_id": v.id,
                                    "content_hash": v.content_hash,
                                    "lock_version": s.lock_version,
                                }
                                for s, v in ((a, av), (b, bv))
                            ],
                            "reason": "identical_body" if exact else "lexical_similarity",
                            "score": round(score, 4),
                            "requires_human_review": True,
                        }
                    )
            return {
                "workspace_id": scope.workspace_id,
                "project_id": scope.project_id,
                "examined_count": min(len(rows), 20),
                "truncated": len(rows) > 20,
                "unavailable_count": unavailable,
                "merge_hints": hints,
                "low_usage": low_usage,
                "automatically_changed": False,
                "semantic_equivalence_established": False,
                "low_usage_means_invalid": False,
            }
