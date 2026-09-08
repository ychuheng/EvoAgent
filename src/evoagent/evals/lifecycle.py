"""评测数据集和实验的状态枚举。"""

from enum import StrEnum


class DatasetStatus(StrEnum):
    DRAFT = "draft"
    FROZEN = "frozen"
    RETIRED = "retired"


class EvalSplit(StrEnum):
    TRAIN = "train"
    HOLDOUT = "holdout"


class EvalExperimentKind(StrEnum):
    SOURCE_VALIDATION = "source_validation"
    SKILL_COMPARISON = "skill_comparison"


class EvalExperimentStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class EvalRunMode(StrEnum):
    BASELINE = "baseline"
    PINNED_SKILL = "pinned_skill"


class PromotionAction(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"
    DISABLE = "disable"
    ENABLE = "enable"
    DEPRECATE = "deprecate"
    ROLLBACK = "rollback"


_DATASET_TRANSITIONS = {
    DatasetStatus.DRAFT: frozenset({DatasetStatus.FROZEN}),
    DatasetStatus.FROZEN: frozenset({DatasetStatus.RETIRED}),
    DatasetStatus.RETIRED: frozenset(),
}

_EXPERIMENT_TRANSITIONS = {
    EvalExperimentStatus.QUEUED: frozenset(
        {EvalExperimentStatus.RUNNING, EvalExperimentStatus.CANCELLED}
    ),
    EvalExperimentStatus.RUNNING: frozenset(
        {
            EvalExperimentStatus.COMPLETED,
            EvalExperimentStatus.FAILED,
            EvalExperimentStatus.CANCELLED,
        }
    ),
    EvalExperimentStatus.COMPLETED: frozenset(),
    EvalExperimentStatus.FAILED: frozenset(),
    EvalExperimentStatus.CANCELLED: frozenset(),
}


def ensure_dataset_transition(current: DatasetStatus, target: DatasetStatus) -> None:
    if target not in _DATASET_TRANSITIONS[current]:
        raise ValueError(f"invalid dataset transition: {current.value} -> {target.value}")


def ensure_experiment_transition(
    current: EvalExperimentStatus, target: EvalExperimentStatus
) -> None:
    if target not in _EXPERIMENT_TRANSITIONS[current]:
        raise ValueError(f"invalid experiment transition: {current.value} -> {target.value}")
