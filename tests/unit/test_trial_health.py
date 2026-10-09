from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from evoagent.skills.health import HEALTH_POLICY, HealthObservation, evaluate_health

NOW = datetime(2026, 10, 9, 12, tzinfo=UTC)
TRIAL, VERSION = uuid4(), uuid4()


def observation(index, **changes):
    return replace(
        HealthObservation(
            id=uuid4(),
            run_id=uuid4(),
            trial_id=TRIAL,
            version_id=VERSION,
            feedback_revision=1,
            first_finished_at=NOW - timedelta(hours=10 - index),
            observed_at=NOW,
            input_fingerprint=str(index),
            outcome="verified_failure",
            attribution="skill_related",
            verification_origin="user",
            criterion_id="identifiers",
            evidence_refs=("user-feedback",),
            associated_steps=("verify",),
        ),
        **changes,
    )


def health(rows, **changes):
    return evaluate_health(
        rows,
        trial_id=TRIAL,
        version_id=VERSION,
        now=NOW,
        policy=changes.get("policy", HEALTH_POLICY),
    )


def test_three_distinct_verified_failures_suspend_but_model_self_judgment_does_not():
    rows = [observation(index) for index in range(3)]
    assert health(rows).status == "suspend"
    assert (
        health([replace(row, verification_origin="model_assistance") for row in rows]).status
        == "healthy"
    )
    assert health([replace(row, attribution="uncertain") for row in rows]).status == "healthy"
    assert health([replace(row, evidence_refs=()) for row in rows]).status == "healthy"


def test_latest_feedback_and_replayed_input_count_once():
    first = observation(0)
    copies = [replace(first, id=uuid4(), feedback_revision=index + 1) for index in range(3)]
    assert health(copies).consecutive_failures == 1
    replays = [observation(index, input_fingerprint="same-input") for index in range(3)]
    assert health(replays).consecutive_failures == 1
    assert (
        health(
            [
                first,
                observation(1),
                observation(2),
                replace(first, id=uuid4(), feedback_revision=2, outcome="verified_success"),
            ]
        ).status
        == "healthy"
    )


def test_success_resets_failure_streak_unknown_does_not_fake_success():
    rows = [observation(0), observation(1), observation(2, outcome="unknown"), observation(3)]
    assert health(rows).status == "suspend"
    rows[2] = replace(rows[2], outcome="verified_success")
    assert health(rows).consecutive_failures == 1


def test_expired_or_other_binding_evidence_does_not_suspend():
    rows = [observation(index, first_finished_at=NOW - timedelta(days=2)) for index in range(3)]
    assert health(rows).status == "healthy"
    assert health([observation(index, trial_id=uuid4()) for index in range(3)]).status == "healthy"


def test_unverifiable_policy_or_scan_overflow_blocks_selection():
    assert (
        health([], policy={**HEALTH_POLICY, "consecutive_failures": 4}).status == "health_pending"
    )
    assert health([observation(0)] * 201).status == "health_pending"
