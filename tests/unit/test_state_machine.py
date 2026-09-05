import pytest

from evoagent.tasks.state_machine import (
    InvalidStateTransitionError,
    PersistentRunStatus,
    TaskStatus,
    ensure_run_transition,
    ensure_task_transition,
)


def test_task_state_machine_accepts_normal_execution_path() -> None:
    ensure_task_transition(TaskStatus.CREATED, TaskStatus.QUEUED)
    ensure_task_transition(TaskStatus.QUEUED, TaskStatus.RUNNING)
    ensure_task_transition(TaskStatus.RUNNING, TaskStatus.COMPLETED)


def test_task_terminal_state_rejects_further_transition() -> None:
    with pytest.raises(InvalidStateTransitionError, match="completed to queued"):
        ensure_task_transition(TaskStatus.COMPLETED, TaskStatus.QUEUED)


def test_waiting_user_must_return_through_queue() -> None:
    ensure_task_transition(TaskStatus.RUNNING, TaskStatus.WAITING_USER)
    ensure_task_transition(TaskStatus.WAITING_USER, TaskStatus.QUEUED)
    with pytest.raises(InvalidStateTransitionError):
        ensure_task_transition(TaskStatus.WAITING_USER, TaskStatus.RUNNING)


def test_run_state_machine_distinguishes_timeout_and_limit() -> None:
    ensure_run_transition(PersistentRunStatus.RUNNING, PersistentRunStatus.TIMEOUT)
    ensure_run_transition(PersistentRunStatus.RUNNING, PersistentRunStatus.LIMIT_REACHED)
    with pytest.raises(InvalidStateTransitionError):
        ensure_run_transition(PersistentRunStatus.TIMEOUT, PersistentRunStatus.RUNNING)
