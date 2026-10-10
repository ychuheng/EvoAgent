from uuid import uuid4

from evoagent.db.models import ArtifactRecord, RunRecord
from evoagent.privacy.redaction import POLICY_VERSION
from evoagent.skills.selection_runtime import resolve_ordinary
from tests.integration.test_skill_selection_runtime import setup_run
from tests.integration.test_skill_selector_candidates import formal_fixture

pytest_plugins = ("tests.integration.test_personal_trials",)


async def test_catalog_uses_actual_selection_and_current_checked_metadata(
    trial_candidate, learning_api
):
    await formal_fixture(trial_candidate)
    client, db, original_id, settings = learning_api
    selector, task, run = await setup_run(trial_candidate, settings)
    choice = await resolve_ordinary(selector, task, run)
    async with db.session_factory() as session:
        current = await session.get(RunRecord, run.id)
        current.status = "completed"
        artifacts = []
        for index, (owner, status, policy, erased) in enumerate(
            (
                (run.id, "verified", POLICY_VERSION, False),
                (run.id, "verified", POLICY_VERSION - 1, False),
                (run.id, "quarantined", POLICY_VERSION, False),
                (run.id, "verified", POLICY_VERSION, True),
                (original_id, "verified", POLICY_VERSION, False),
            )
        ):
            artifact = ArtifactRecord(
                run_id=owner,
                type="tool_output",
                uri=f"private://not-returned/{index}",
                size_bytes=1,
                content_hash="sha256:" + "a" * 64,
                redaction_status=status,
                redaction_policy_version=policy,
                redaction_checked_hash="sha256:" + "a" * 64,
                attributes={"erased": erased, "secret_metadata": "never-returned"},
            )
            session.add(artifact)
            artifacts.append(artifact)
        await session.commit()
    response = await client.get(f"/api/v1/runs/{run.id}/skill-observation-evidence")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["versions"][0]["version_id"] == str(choice.selections[0].version_id)
    assert body["versions"][0]["steps"]
    assert body["artifacts"] == [
        {
            "artifact_id": str(artifacts[0].id),
            "content_hash": artifacts[0].content_hash,
            "type": "tool_output",
        }
    ]
    assert not body["artifacts_truncated"]
    assert "private://" not in response.text and "secret_metadata" not in response.text


async def test_catalog_refuses_missing_running_and_nonpersonal_runs(learning_api):
    client, db, run_id, _ = learning_api
    assert (
        await client.get(f"/api/v1/runs/{uuid4()}/skill-observation-evidence")
    ).status_code == 404
    for values in ({"status": "running"}, {"status": "completed", "data_role": "train"}):
        async with db.session_factory() as session:
            original = await session.get(RunRecord, run_id)
            run = RunRecord(task_id=original.task_id, provider="mock", model="mock", **values)
            session.add(run)
            await session.commit()
        response = await client.get(f"/api/v1/runs/{run.id}/skill-observation-evidence")
        assert response.status_code == 422
        assert response.json()["detail"]["code"] == "observation_terminal_personal_run_required"
