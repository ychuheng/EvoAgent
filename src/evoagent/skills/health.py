"""Deterministic trial health, using independent verified observations only."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

HEALTH_POLICY = {
    "version": 1,
    "window_hours": 24,
    "consecutive_failures": 3,
    "maximum_observations": 200,
    "dedupe": "latest_feedback_per_run_then_latest_run_per_input",
    "ordering": "first_finished_at_then_run_id",
}


@dataclass(frozen=True)
class HealthObservation:
    id: UUID
    run_id: UUID
    trial_id: UUID
    version_id: UUID
    feedback_revision: int
    first_finished_at: datetime
    observed_at: datetime
    input_fingerprint: str
    outcome: str
    attribution: str
    verification_origin: str
    criterion_id: str
    evidence_refs: tuple[str, ...]
    associated_steps: tuple[str, ...]


@dataclass(frozen=True)
class TrialHealth:
    status: str  # healthy, suspend, health_pending
    evidence_ids: tuple[UUID, ...] = ()
    consecutive_failures: int = 0


def evaluate_health(observations, *, trial_id, version_id, now, policy):
    if policy != HEALTH_POLICY or now.tzinfo is None:
        return TrialHealth("health_pending")
    cutoff = now - timedelta(hours=policy["window_hours"])
    relevant = []
    for item in observations:
        if item.trial_id != trial_id or item.version_id != version_id:
            continue
        if item.first_finished_at.tzinfo is None or item.observed_at.tzinfo is None:
            return TrialHealth("health_pending")
        if cutoff <= item.first_finished_at <= now and item.observed_at <= now:
            relevant.append(item)
    if len(relevant) > policy["maximum_observations"]:
        return TrialHealth("health_pending")
    latest = {}
    for item in relevant:
        prior = latest.get(item.run_id)
        if prior is None or (item.feedback_revision, item.observed_at, item.id.hex) > (
            prior.feedback_revision,
            prior.observed_at,
            prior.id.hex,
        ):
            latest[item.run_id] = item
    by_input = {}
    for item in sorted(latest.values(), key=lambda row: (row.first_finished_at, row.run_id.hex)):
        if not item.input_fingerprint:
            continue  # no replay identity is insufficient evidence
        by_input[item.input_fingerprint] = item
    failures = []
    for item in sorted(by_input.values(), key=lambda row: (row.first_finished_at, row.run_id.hex)):
        if (
            item.verification_origin not in {"machine", "user"}
            or not item.criterion_id
            or not item.evidence_refs
            or not item.associated_steps
        ):
            continue
        if item.outcome == "verified_success":
            failures.clear()
        elif item.outcome == "verified_failure" and item.attribution == "skill_related":
            failures.append(item.id)
    threshold = policy["consecutive_failures"]
    return TrialHealth(
        "suspend" if len(failures) >= threshold else "healthy",
        tuple(failures[-threshold:]),
        len(failures),
    )
