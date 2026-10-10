"""Usage history is scoped, paged and separate from efficacy evidence."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from evoagent.learning.repository import LearningRepository
from evoagent.learning.schema import FeedbackPayload, LearningError
from evoagent.skills.trials import TrialScope
from evoagent.skills.usage import SkillUsageService
from tests.integration.test_skill_usage_collection import setup_use

pytest_plugins = ("tests.integration.test_personal_trials",)


async def test_latest_feedback_summary_does_not_count_revisions_as_tasks(trial_candidate):
    client, _, _, _, _, skill = trial_candidate
    db, run, version, artifact = await setup_use(trial_candidate)
    usage = SkillUsageService(db.session_factory)
    scope = TrialScope(skill.workspace_id)
    await usage.collect(run.id)
    claim = {
        "type": "skill_observation",
        "schema_version": 1,
        "version_id": str(version.id),
        "criterion_id": "identifiers_preserved",
        "outcome": "verified_failure",
        "attribution": "skill_related",
        "associated_steps": [version.definition["steps"][0]["id"]],
        "artifacts": [{"artifact_id": str(artifact.id), "content_hash": artifact.content_hash}],
    }
    async with db.session_factory() as session:
        feedback = await LearningRepository(session).append_feedback(
            run.id,
            "report-failure",
            FeedbackPayload(intent="method", verdict="incorrect", evidence_refs=[claim]),
            "local-user",
        )
        await session.commit()
    await usage.collect(run.id, feedback.revision)
    summary = await usage.summarize(skill.id, scope)
    assert summary["selected_count"] == summary["projected_count"] == 1
    assert summary["outcomes"] == {"verified_success": 0, "verified_failure": 1, "unknown": 0}
    assert summary["skill_related_failures"] == 1
    assert summary["token_coverage"] == "settled_spend_records_only"
    assert summary["spend_record_count"] == 0
    assert summary["elapsed_mean_seconds"] is None
    assert summary["causal_benefit_established"] is False
    response = await client.get(
        f"/api/v1/skills/{skill.id}/usage-summary", params={"workspace_id": str(scope.workspace_id)}
    )
    assert response.status_code == 200 and response.json()["selected_count"] == 1
    first = await usage.list_evidence(version.id, scope, limit=1)
    assert len(first["items"]) == 1 and first["next_cursor"] is not None
    second = await usage.list_evidence(version.id, scope, cursor=first["next_cursor"], limit=1)
    assert len(second["items"]) == 1 and second["next_cursor"] is None
    assert first["items"][0]["id"] != second["items"][0]["id"]
    assert "evidence" not in first["items"][0]
    assert "definition" not in str(first)
    api = await client.get(
        f"/api/v1/skill-versions/{version.id}/usage-evidence",
        params={"workspace_id": str(scope.workspace_id), "limit": 1},
    )
    assert api.status_code == 200 and len(api.json()["items"]) == 1


async def test_usage_scope_and_time_bounds_reject_or_exclude(trial_candidate):
    client, _, _, _, _, skill = trial_candidate
    db, run, version, _ = await setup_use(trial_candidate)
    usage = SkillUsageService(db.session_factory)
    scope = TrialScope(skill.workspace_id)
    await usage.collect(run.id)
    with pytest.raises(LearningError, match="usage_scope_invalid"):
        await usage.summarize(skill.id, TrialScope(uuid4()))
    with pytest.raises(LearningError, match="usage_since_timezone_required"):
        await usage.summarize(skill.id, scope, datetime(2026, 1, 1))
    with pytest.raises(LearningError, match="usage_evidence_limit_invalid"):
        await usage.list_evidence(version.id, scope, limit=101)
    scoped = await usage.summarize(skill.id, TrialScope(skill.workspace_id, uuid4()))
    assert scoped["selected_count"] == scoped["projected_count"] == 0
    after = await usage.summarize(skill.id, scope, datetime.now(UTC))
    assert after["selected_count"] == 0
    response = await client.get(
        f"/api/v1/skills/{skill.id}/usage-summary", params={"workspace_id": str(uuid4())}
    )
    assert (
        response.status_code == 422 and response.json()["detail"]["code"] == "usage_scope_invalid"
    )
