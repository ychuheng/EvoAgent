"""Skill 聚合与版本的纯 Python 状态机。"""

from enum import StrEnum


class SkillStatus(StrEnum):
    ENABLED = "enabled"
    DISABLED = "disabled"
    DEPRECATED = "deprecated"


class SkillVersionStatus(StrEnum):
    DRAFT = "draft"
    EVALUATING = "evaluating"
    REVIEW_REQUIRED = "review_required"
    ACTIVE = "active"
    RETIRED = "retired"
    REJECTED = "rejected"


_SKILL_TRANSITIONS = {
    SkillStatus.ENABLED: frozenset({SkillStatus.DISABLED, SkillStatus.DEPRECATED}),
    SkillStatus.DISABLED: frozenset({SkillStatus.ENABLED, SkillStatus.DEPRECATED}),
    SkillStatus.DEPRECATED: frozenset(),
}

_VERSION_TRANSITIONS = {
    SkillVersionStatus.DRAFT: frozenset(
        {SkillVersionStatus.EVALUATING, SkillVersionStatus.REJECTED}
    ),
    SkillVersionStatus.EVALUATING: frozenset(
        {SkillVersionStatus.REVIEW_REQUIRED, SkillVersionStatus.REJECTED}
    ),
    SkillVersionStatus.REVIEW_REQUIRED: frozenset(
        {SkillVersionStatus.ACTIVE, SkillVersionStatus.REJECTED}
    ),
    SkillVersionStatus.ACTIVE: frozenset({SkillVersionStatus.RETIRED}),
    SkillVersionStatus.RETIRED: frozenset({SkillVersionStatus.ACTIVE}),
    SkillVersionStatus.REJECTED: frozenset(),
}


def ensure_skill_transition(current: SkillStatus, target: SkillStatus) -> None:
    if target not in _SKILL_TRANSITIONS[current]:
        raise ValueError(f"invalid skill transition: {current.value} -> {target.value}")


def ensure_skill_version_transition(
    current: SkillVersionStatus, target: SkillVersionStatus
) -> None:
    if target not in _VERSION_TRANSITIONS[current]:
        raise ValueError(f"invalid skill version transition: {current.value} -> {target.value}")
