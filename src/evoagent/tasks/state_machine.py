"""Task 与 Run 的合法状态迁移规则。"""

from enum import StrEnum


class TaskStatus(StrEnum):
    CREATED = "created"
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_TOOL = "waiting_tool"
    WAITING_USER = "waiting_user"
    RETRYING = "retrying"
    PAUSED = "paused"
    RECOVERING = "recovering"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class PersistentRunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_USER = "waiting_user"
    RETRYING = "retrying"
    PAUSED = "paused"
    RECOVERING = "recovering"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"
    LIMIT_REACHED = "limit_reached"


class InvalidStateTransitionError(ValueError):
    """请求的状态变化不符合已固定的状态机。"""


_TASK_TRANSITIONS: dict[TaskStatus, frozenset[TaskStatus]] = {
    TaskStatus.CREATED: frozenset({TaskStatus.QUEUED, TaskStatus.CANCELLED}),
    TaskStatus.QUEUED: frozenset({TaskStatus.RUNNING, TaskStatus.PAUSED, TaskStatus.CANCELLED}),
    TaskStatus.RUNNING: frozenset(
        {
            TaskStatus.WAITING_TOOL,
            TaskStatus.WAITING_USER,
            TaskStatus.RETRYING,
            TaskStatus.PAUSED,
            TaskStatus.RECOVERING,
            TaskStatus.COMPLETED,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
        }
    ),
    TaskStatus.WAITING_TOOL: frozenset(
        {TaskStatus.RUNNING, TaskStatus.RECOVERING, TaskStatus.FAILED, TaskStatus.CANCELLED}
    ),
    TaskStatus.WAITING_USER: frozenset({TaskStatus.QUEUED, TaskStatus.CANCELLED}),
    TaskStatus.RETRYING: frozenset({TaskStatus.QUEUED, TaskStatus.CANCELLED}),
    TaskStatus.PAUSED: frozenset({TaskStatus.QUEUED, TaskStatus.CANCELLED}),
    TaskStatus.RECOVERING: frozenset(
        {
            TaskStatus.QUEUED,
            TaskStatus.WAITING_USER,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
        }
    ),
    TaskStatus.COMPLETED: frozenset(),
    TaskStatus.FAILED: frozenset(),
    TaskStatus.CANCELLED: frozenset(),
}

_RUN_TRANSITIONS: dict[PersistentRunStatus, frozenset[PersistentRunStatus]] = {
    PersistentRunStatus.QUEUED: frozenset(
        {
            PersistentRunStatus.RUNNING,
            PersistentRunStatus.PAUSED,
            PersistentRunStatus.CANCELLED,
        }
    ),
    PersistentRunStatus.RUNNING: frozenset(
        {
            PersistentRunStatus.WAITING_USER,
            PersistentRunStatus.RETRYING,
            PersistentRunStatus.PAUSED,
            PersistentRunStatus.RECOVERING,
            PersistentRunStatus.COMPLETED,
            PersistentRunStatus.FAILED,
            PersistentRunStatus.CANCELLED,
            PersistentRunStatus.TIMEOUT,
            PersistentRunStatus.LIMIT_REACHED,
        }
    ),
    PersistentRunStatus.WAITING_USER: frozenset(
        {PersistentRunStatus.QUEUED, PersistentRunStatus.CANCELLED}
    ),
    PersistentRunStatus.RETRYING: frozenset(
        {PersistentRunStatus.QUEUED, PersistentRunStatus.CANCELLED}
    ),
    PersistentRunStatus.PAUSED: frozenset(
        {PersistentRunStatus.QUEUED, PersistentRunStatus.CANCELLED}
    ),
    PersistentRunStatus.RECOVERING: frozenset(
        {
            PersistentRunStatus.QUEUED,
            PersistentRunStatus.WAITING_USER,
            PersistentRunStatus.FAILED,
            PersistentRunStatus.CANCELLED,
        }
    ),
    PersistentRunStatus.COMPLETED: frozenset(),
    PersistentRunStatus.FAILED: frozenset(),
    PersistentRunStatus.CANCELLED: frozenset(),
    PersistentRunStatus.TIMEOUT: frozenset(),
    PersistentRunStatus.LIMIT_REACHED: frozenset(),
}


def ensure_task_transition(current: TaskStatus, target: TaskStatus) -> None:
    """拒绝 Task 状态机中不存在的迁移。"""

    if target not in _TASK_TRANSITIONS[current]:
        raise InvalidStateTransitionError(
            f"task cannot transition from {current.value} to {target.value}"
        )


def ensure_run_transition(current: PersistentRunStatus, target: PersistentRunStatus) -> None:
    """拒绝 Run 状态机中不存在的迁移。"""

    if target not in _RUN_TRANSITIONS[current]:
        raise InvalidStateTransitionError(
            f"run cannot transition from {current.value} to {target.value}"
        )
