"""Trial safety contracts; seeded bindings are not evidence of validation readiness."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from evoagent.db.models import (
    RunRecord,
    RunSkillSelectionRecord,
    SkillEventRecord,
    SkillObservationRecord,
    SkillRecord,
    SkillTrialRecord,
    SkillVersionRecord,
)
from evoagent.skills.canonical import content_hash
from evoagent.skills.health import HEALTH_POLICY
from evoagent.skills.trials import SkillTrialService, TrialError, TrialScope
from tests.integration.test_learning_worker import submit, worker

pytest_plugins = ("tests.integration.test_learning_api",)


@pytest.fixture
async def trial_candidate(learning_api):
    client, db, run_id, settings = learning_api
    request = await submit(client, run_id)
    runner = worker(db, settings)
    for _ in range(3):
        assert await runner.run_once()
    state = (await client.get(f"/api/v1/learning-requests/{request['id']}")).json()
    async with db.session_factory() as session:
        version = await session.get(SkillVersionRecord, UUID(state["candidate_version_id"]))
        skill = await session.get(SkillRecord, version.skill_id)
    return client, db, run_id, state, version, skill


async def seed_binding(context):
    # Deliberately bypass activate only to test guards on an existing binding.
    # The fixture's P2 report is insufficient for real trial admission.
    _, db, _, state, version, skill = context
    trial = SkillTrialRecord(
        skill_id=skill.id,
        version_id=version.id,
        workspace_id=skill.workspace_id,
        project_id=None,
        scope_key=TrialScope(skill.workspace_id).key,
        validation_request_id=UUID(state["id"]),
        report_hash=state["validation_report_hash"],
        health_policy_snapshot=deepcopy(HEALTH_POLICY),
        health_policy_hash=content_hash(HEALTH_POLICY),
        reviewer="fixture",
        reason="existing binding for safety tests",
    )
    async with db.session_factory() as session:
        session.add(trial)
        await session.commit()
    return trial


async def test_candidate_review_cannot_be_misrepresented_as_trial_validation(trial_candidate):
    _, db, _, state, version, skill = trial_candidate
    service = SkillTrialService(db.session_factory, trial_enabled=True)
    readiness = await service.assess(version.id, UUID(state["id"]))
    assert not readiness.ready
    assert readiness.reasons == ("independent_personal_validation_required",)
    with pytest.raises(TrialError, match="independent_personal_validation_required"):
        await service.activate(
            version.id, TrialScope(skill.workspace_id), UUID(state["id"]), 0, "user", "review"
        )
    async with db.session_factory() as session:
        assert not list(await session.scalars(select(SkillTrialRecord)))
        assert (await session.get(SkillRecord, skill.id)).active_version_id is None


async def test_feature_stays_closed_until_validation_selection_and_health_are_connected(
    trial_candidate,
):
    _, db, _, state, version, skill = trial_candidate
    service = SkillTrialService(db.session_factory)
    with pytest.raises(TrialError, match="feature_not_ready"):
        await service.activate(
            version.id, TrialScope(skill.workspace_id), UUID(state["id"]), 0, "user", "review"
        )


async def test_active_binding_uniqueness_and_frozen_policy(trial_candidate):
    trial = await seed_binding(trial_candidate)
    _, db, _, _, _, _ = trial_candidate
    async with db.session_factory() as session:
        copy = SkillTrialRecord(
            skill_id=trial.skill_id,
            version_id=trial.version_id,
            workspace_id=trial.workspace_id,
            scope_key=trial.scope_key,
            validation_request_id=trial.validation_request_id,
            report_hash=trial.report_hash,
            health_policy_snapshot=deepcopy(HEALTH_POLICY),
            health_policy_hash=trial.health_policy_hash,
            reviewer="fixture",
            reason="collision",
        )
        session.add(copy)
        with pytest.raises(IntegrityError):
            await session.commit()
    async with db.session_factory() as session:
        current = await session.get(SkillTrialRecord, trial.id)
        current.health_policy_snapshot = {**HEALTH_POLICY, "consecutive_failures": 100}
        with pytest.raises(ValueError, match="immutable"):
            await session.commit()


async def failure_observations(context, trial, *, attribution="skill_related"):
    _, db, run_id, _, version, _ = context
    now = datetime.now(UTC)
    identities = []
    async with db.session_factory() as session:
        original = await session.get(RunRecord, run_id)
        for index in range(3):
            run = RunRecord(
                task_id=original.task_id,
                provider="mock",
                model="mock",
                data_role="personal",
                status="completed",
                ended_at=now - timedelta(minutes=3 - index),
            )
            session.add(run)
            await session.flush()
            selection = RunSkillSelectionRecord(
                run_id=run.id,
                skill_version_id=version.id,
                trial_id=trial.id,
                origin="trial",
                mode="retrieval",
                rank=1,
                score=1,
                query_terms=[],
                scope_key=trial.scope_key,
            )
            session.add(selection)
            await session.flush()
            observation = SkillObservationRecord(
                run_id=run.id,
                version_id=version.id,
                trial_id=trial.id,
                selection_id=selection.id,
                feedback_revision=1,
                outcome="verified_failure",
                attribution=attribution,
                first_finished_at=run.ended_at,
                input_fingerprint=content_hash({"input": index}),
                evidence={
                    "verification_origin": "user",
                    "criterion_id": "identifier-preservation",
                    "evidence_refs": [str(uuid4())],
                    "associated_steps": [version.definition["steps"][0]["id"]],
                },
            )
            session.add(observation)
            await session.flush()
            identities.append(observation.id)
        await session.commit()
    return identities


async def test_auto_suspend_is_durable_and_idempotent_without_learning_enabled(trial_candidate):
    trial = await seed_binding(trial_candidate)
    ids = await failure_observations(trial_candidate, trial)
    _, db, _, _, version, _ = trial_candidate
    service = SkillTrialService(db.session_factory)
    assert await service.auto_suspend(
        trial.id,
        policy_hash=trial.health_policy_hash,
        observation_ids=ids,
        reason="verified repeated failures",
    )
    assert not await service.auto_suspend(
        trial.id, policy_hash=trial.health_policy_hash, observation_ids=ids, reason="repeat"
    )
    async with db.session_factory() as session:
        stored = await session.get(SkillTrialRecord, trial.id)
        assert stored.status == "suspended" and stored.lock_version == 1
        assert len(list(await session.scalars(select(SkillObservationRecord)))) == 3
        events = list(
            await session.scalars(
                select(SkillEventRecord).where(
                    SkillEventRecord.event_type == "skill.trial_auto_suspended"
                )
            )
        )
        assert len(events) == 1 and set(events[0].payload["observation_ids"]) == {
            str(item) for item in ids
        }
        assert (await session.get(SkillVersionRecord, version.id)).definition == version.definition


async def test_uncertain_attribution_does_not_suspend_and_source_revocation_does(trial_candidate):
    client, db, _, state, _, _ = trial_candidate
    trial = await seed_binding(trial_candidate)
    ids = await failure_observations(trial_candidate, trial, attribution="uncertain")
    service = SkillTrialService(db.session_factory)
    assert not await service.auto_suspend(
        trial.id, policy_hash=trial.health_policy_hash, observation_ids=ids, reason="uncertain"
    )
    assert not await service.suspend_unavailable(trial.id)
    response = await client.post(
        f"/api/v1/learning-sources/{state['source']['id']}/revoke",
        json={"reason": "withdraw", "expected_status": "valid"},
    )
    assert response.status_code == 200
    assert await service.suspend_unavailable(trial.id)
    assert not await service.suspend_unavailable(trial.id)


@pytest.mark.parametrize("reduce_policy", [False, True])
async def test_shared_extraction_honors_frozen_and_current_source_risk(learning_api, reduce_policy):
    from evoagent.db.models import DEFAULT_WORKSPACE_ID, ToolCallRecord, TurnRecord

    client, db, run_id, settings = learning_api
    response = await client.put(
        f"/api/v1/workspaces/{DEFAULT_WORKSPACE_ID}/learning-policy",
        json={"mode": "manual", "expected_lock_version": 0, "max_source_risk": "R2"},
    )
    assert response.status_code == 200
    async with db.session_factory() as session:
        turn = TurnRecord(run_id=run_id, sequence=1, status="completed")
        session.add(turn)
        await session.flush()
        session.add(
            ToolCallRecord(
                run_id=run_id,
                turn_id=turn.id,
                provider_call_id="source-edit",
                tool_name="edit_file",
                arguments={},
                risk="R2",
                status="succeeded",
            )
        )
        await session.commit()
    response = await client.post(
        f"/api/v1/runs/{run_id}/learning-requests", json={"client_request_id": "risk-policy"}
    )
    assert response.status_code == 202, response.text
    request_id = response.json()["id"]
    runner = worker(db, settings)
    assert await runner.run_once()
    if reduce_policy:
        changed = await client.put(
            f"/api/v1/workspaces/{DEFAULT_WORKSPACE_ID}/learning-policy",
            json={"mode": "manual", "expected_lock_version": 1, "max_source_risk": "R1"},
        )
        assert changed.status_code == 200
    assert await runner.run_once()
    if not reduce_policy:
        assert await runner.run_once()
    state = (await client.get(f"/api/v1/learning-requests/{request_id}")).json()
    if reduce_policy:
        assert state["status"] == "failed" and state["error_code"] == "source_risk_exceeds_policy"
    else:
        assert state["status"] == "ready_for_review", state
