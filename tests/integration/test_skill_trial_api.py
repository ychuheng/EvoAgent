from uuid import UUID

from sqlalchemy import func, select

from evoagent.db.models import SkillEventRecord, SkillRecord, SkillTrialRecord
from tests.integration.test_personal_trials import seed_binding

pytest_plugins = ("tests.integration.test_personal_trials",)


async def test_activation_stays_gated_and_mock_review_cannot_gain_trial(
    trial_candidate, learning_api
):
    client, db, _, state, version, skill = trial_candidate
    _, _, _, settings = learning_api
    body = {
        "workspace_id": str(skill.workspace_id),
        "validation_request_id": state["id"],
        "expected_lock_version": skill.lock_version,
        "reason": "reviewed method",
    }
    path = f"/api/v1/skill-versions/{version.id}/trial"
    response = await client.post(path, json=body)
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "trial_feature_not_ready"
    settings.personal_trial_enabled = True
    response = await client.post(path, json=body)
    assert response.status_code == 422
    assert response.json()["detail"] == {
        "code": "trial_not_ready",
        "reason": "independent_personal_validation_required",
    }
    response = await client.get(
        f"/api/v1/skill-versions/{version.id}/trial-readiness",
        params={"validation_request_id": state["id"]},
    )
    assert response.status_code == 200
    assert not response.json()["ready"] and not response.json()["evidence_ready"]
    async with db.session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(SkillTrialRecord)) == 0
        assert (await session.get(SkillRecord, skill.id)).active_version_id is None


async def test_manual_brake_is_available_with_adoption_off_and_enforces_cas(trial_candidate):
    client, db, _, _, _, skill = trial_candidate
    # Safety fixture seeds a binding without real-model admission. This tests
    # stopping an existing binding, never its efficacy or eligibility.
    trial = await seed_binding(trial_candidate)
    response = await client.get(
        "/api/v1/skill-trials", params={"workspace_id": str(skill.workspace_id)}
    )
    assert response.status_code == 200
    assert [item["id"] for item in response.json()["items"]] == [str(trial.id)]
    path = f"/api/v1/skill-trials/{trial.id}/suspend"
    body = {"expected_lock_version": 0, "reason": "user verified a wrong method"}
    response = await client.post(path, json=body)
    assert response.status_code == 200
    assert response.json()["status"] == "suspended"
    assert response.json()["lock_version"] == 1
    assert (await client.post(path, json=body)).status_code == 409
    async with db.session_factory() as session:
        events = list(
            await session.scalars(
                select(SkillEventRecord).where(
                    SkillEventRecord.event_type == "skill.trial_suspended"
                )
            )
        )
        assert len(events) == 1 and events[0].payload["actor"] == "local-user"
        assert (await session.get(SkillRecord, skill.id)).active_version_id is None


async def test_trial_controls_reject_client_actor_and_out_of_scope_rollback(trial_candidate):
    client, _, _, _, _, skill = trial_candidate
    trial = await seed_binding(trial_candidate)
    response = await client.post(
        f"/api/v1/skill-trials/{trial.id}/suspend",
        json={"expected_lock_version": 0, "reason": "review", "actor": "model"},
    )
    assert response.status_code == 422
    response = await client.post(
        f"/api/v1/skill-trials/{trial.id}/rollback",
        json={
            "expected_lock_version": 0,
            "reason": "rollback",
            "target_trial_id": str(UUID(int=0)),
        },
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "trial_rollback_target_conflict"
    response = await client.get(
        "/api/v1/skill-trials", params={"workspace_id": str(skill.workspace_id), "limit": 101}
    )
    assert response.status_code == 422
