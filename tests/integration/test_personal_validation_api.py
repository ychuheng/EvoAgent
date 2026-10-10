"""Local user endpoints freeze, dispatch and judge without granting trial."""

from uuid import UUID

from sqlalchemy import func, select

from evoagent.db.models import MaintenanceJobRecord, SkillTrialRecord, ValidationJudgmentRecord
from tests.integration.test_validation_admission import inputs
from tests.integration.test_validation_judgments import completed_report

pytest_plugins = ("tests.integration.test_personal_trials",)


async def test_http_freeze_and_explicit_dispatch_are_separate_and_replay_safe(trial_candidate):
    client, db, *_ = trial_candidate
    _, parent, payload, _ = await inputs(trial_candidate)
    assert (
        "prepare_validation"
        in (await client.get(f"/api/v1/learning-requests/{parent.id}")).json()["available_actions"]
    )
    body = payload.model_dump(mode="json")
    endpoint = f"/api/v1/learning-requests/{parent.id}/validations"
    denied = await client.post(endpoint, json={**body, "provider": "real-provider"})
    assert denied.status_code == 422
    first = await client.post(endpoint, json=body)
    assert first.status_code == 202, first.text
    child = first.json()
    assert first.headers["location"].endswith(child["id"])
    assert child["policy_snapshot"]["provider"] == "mock"
    assert "start_validation" in child["available_actions"]
    assert (await client.post(endpoint, json=body)).json()["id"] == child["id"]
    async with db.session_factory() as session:
        assert (
            await session.scalar(
                select(func.count())
                .select_from(MaintenanceJobRecord)
                .where(MaintenanceJobRecord.learning_request_id == UUID(child["id"]))
            )
            == 0
        )
    start = f"/api/v1/learning-requests/{child['id']}/validation-start"
    for invalid in (
        {"expected_lock_version": True},
        {"expected_lock_version": child["lock_version"], "trial_eligible": True},
    ):
        assert (await client.post(start, json=invalid)).status_code == 422
    valid = {"expected_lock_version": child["lock_version"]}
    started = await client.post(start, json=valid)
    assert started.status_code == 202, started.text
    assert (await client.post(start, json=valid)).json() == started.json()
    async with db.session_factory() as session:
        assert (
            await session.scalar(
                select(func.count())
                .select_from(MaintenanceJobRecord)
                .where(MaintenanceJobRecord.learning_request_id == UUID(child["id"]))
            )
            == 1
        )
        assert not list(await session.scalars(select(SkillTrialRecord)))


async def test_http_actual_report_user_judgments_cannot_grant_trial_or_actor(trial_candidate):
    client = trial_candidate[0]
    db, _, row = await completed_report(trial_candidate)
    detail = (await client.get(f"/api/v1/learning-requests/{row.id}")).json()
    assert detail["available_actions"] == ["judge_validation"]
    body = {
        "client_request_id": "http-judgments",
        "expected_lock_version": row.lock_version,
        "expected_report_hash": row.validation_report_hash,
        "judgments": [
            {
                "eval_run_id": next(
                    ref["id"] for ref in item["evidence_refs"] if ref["type"] == "eval_run"
                ),
                "criterion_id": item["criterion_id"],
                "verdict": "pass",
                "observed": {"reviewed_output": True},
                "reason": "User inspected frozen business criteria",
            }
            for item in row.validation_report["items"]
        ],
    }
    endpoint = f"/api/v1/learning-requests/{row.id}/judgments"
    assert (await client.post(endpoint, json={**body, "actor": "model"})).status_code == 422
    first = await client.post(endpoint, json=body)
    assert first.status_code == 201, first.text
    assert first.json()["actor"] == "local-user" and not first.json()["trial_eligible"]
    assert (await client.post(endpoint, json=body)).json() == first.json()
    stale = await client.post(endpoint, json={**body, "client_request_id": "stale-http"})
    assert stale.status_code == 409
    async with db.session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(ValidationJudgmentRecord)) == 1
        assert not list(await session.scalars(select(SkillTrialRecord)))


async def test_http_invalid_sensitive_input_is_not_reflected(trial_candidate):
    client = trial_candidate[0]
    _, parent, payload, _ = await inputs(trial_candidate)
    body = payload.model_dump(mode="json")
    secret = "private-test-value-not-a-credential"
    body["cases"][0]["public_input"]["inputs"] = {"password": secret}
    response = await client.post(f"/api/v1/learning-requests/{parent.id}/validations", json=body)
    assert response.status_code == 422 and secret not in response.text
