"""反馈意图的确定性路由；混合或未确定意图不自动启动付费提炼。"""

from dataclasses import dataclass
from typing import Literal

from evoagent.learning.schema import FeedbackPayload, LearningError
from evoagent.skills.canonical import content_hash


def route_feedback(payload: FeedbackPayload) -> Literal["method", "fact", "clarify", "none"]:
    if not payload.learn_from_feedback:
        return "none"
    if payload.intent in {"mixed", "unsure"}:
        return "clarify"
    return payload.intent


@dataclass(frozen=True, slots=True)
class EvolutionDecision:
    action: Literal["create", "revise", "duplicate", "skip"]
    definition: object | None = None
    duplicate_version_id: object | None = None


class SkillEvolutionPlanner:
    """The host selects revision targets; model names never grant target ownership."""

    def validate_target(self, request, skill, base):
        if request.target_skill_id is None:
            if request.base_version_id is not None:
                raise LearningError("revision_target_required")
            return
        if (
            skill is None
            or base is None
            or skill.id != request.target_skill_id
            or base.id != request.base_version_id
            or base.skill_id != skill.id
            or skill.workspace_id != request.workspace_id
            or skill.project_id not in (None, request.project_id)
        ):
            raise LearningError("revision_target_invalid")
        if content_hash(base.definition) != base.content_hash:
            raise LearningError("revision_base_hash_mismatch")

    def find_duplicates(self, definition, candidates):
        digest = content_hash(definition.model_dump(mode="json"))
        return next((row for row in candidates if row.content_hash == digest), None)

    def suggest(self, definition, existing_skills, *, revising=False):
        duplicate = self.find_duplicates(definition, existing_skills)
        if duplicate is not None:
            return EvolutionDecision("duplicate", definition, duplicate.id)
        return EvolutionDecision("revise" if revising else "create", definition)
