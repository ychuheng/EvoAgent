"""Actual injected-provider validation followed by explicit merge supersession.

Paid calls and human judgments are synthetic fixtures, never efficacy evidence.
"""

from uuid import UUID

import pytest
from sqlalchemy import select

from evoagent.db.models import SkillEventRecord, SkillRecord, SkillTrialRecord, SkillVersionRecord
from evoagent.memory.maintenance import MaintenanceWorker
from evoagent.skills.access import SkillAccessError, SkillAccessPolicy
from evoagent.skills.lifecycle import SkillStatus
from evoagent.skills.observation_jobs import ObservationJobHandler
from tests.integration import test_real_validation_contract as real_contract
from tests.integration.test_personal_trials import seed_binding
from tests.integration.test_skill_merge_proposals import merge_body

pytest_plugins = ("tests.integration.test_personal_selection_v3",)


async def merged_context(context):
    client, db, run_id, *_ = context
    body = await merge_body(context)
    response = await client.post("/api/v1/skills/merge-proposals", json=body)
    assert response.status_code == 202, response.text
    state = (await client.get(f"/api/v1/learning-requests/{response.json()['id']}")).json()
    async with db.session_factory() as session:
        version = await session.get(SkillVersionRecord, UUID(state["candidate_version_id"]))
        skill = await session.get(SkillRecord, version.skill_id)
    return (client, db, run_id, state, version, skill), body


async def payload(context, old_id, trial):
    _, db, _, _, version, replacement = context
    async with db.session_factory() as session:
        old = await session.get(SkillRecord, old_id)
        new = await session.get(SkillRecord, replacement.id)
    return {
        "replacement_id": str(new.id),
        "replacement_version_id": str(version.id),
        "replacement_trial_id": str(trial.id),
        "expected_lock_version": old.lock_version,
        "expected_replacement_lock_version": new.lock_version,
        "expected_trial_lock_version": trial.lock_version,
        "reason": "independently validated combined method replaces old binding",
    }


async def test_review_only_merge_cannot_deprecate_existing_method(selection_candidate):
    context, _ = await merged_context(selection_candidate)
    client, db, _, _, _, old = selection_candidate
    seeded = await seed_binding(
        context
    )  # Deliberately insufficient readiness, for the negative test.
    body = await payload(context, old.id, seeded)
    response = await client.post(f"/api/v1/skills/{old.id}/supersede", json=body)
    assert response.status_code == 422, response.text
    assert "supersession_verified_validation_required" in response.text
    async with db.session_factory() as session:
        current = await session.get(SkillRecord, old.id)
        assert current.status is SkillStatus.ENABLED and current.superseded_by_skill_id is None


async def test_validated_merge_supersession_preserves_lineage_but_not_revoked_sources(
    selection_candidate, monkeypatch, tmp_path
):
    context, originals = await merged_context(selection_candidate)
    await (
        real_contract.test_real_validation_uses_task_bound_learning_ledger_without_double_charging(
            context, monkeypatch, tmp_path
        )
    )
    client, db, _, _, merged_version, merged_skill = context
    async with db.session_factory() as session:
        pending_trial = await session.scalar(
            select(SkillTrialRecord).where(SkillTrialRecord.skill_id == merged_skill.id)
        )
    pending_body = await payload(context, selection_candidate[5].id, pending_trial)
    pending_denied = await client.post(
        f"/api/v1/skills/{selection_candidate[5].id}/supersede", json=pending_body
    )
    assert pending_denied.status_code == 422, pending_denied.text
    assert "supersession_healthy_replacement_required" in pending_denied.text
    safety = MaintenanceWorker(
        db.session_factory,
        None,
        lane="critical",
        handlers={"learning_observe": ObservationJobHandler(db.session_factory)},
        allowed_kinds={"learning_observe"},
    )
    while await safety.run_once():
        pass
    old_binding = await seed_binding(selection_candidate)
    async with db.session_factory() as session:
        trial = await session.scalar(
            select(SkillTrialRecord).where(SkillTrialRecord.skill_id == merged_skill.id)
        )
    for parent in originals["parents"]:
        old_id = UUID(parent["skill_id"])
        body = await payload(context, old_id, trial)
        response = await client.post(f"/api/v1/skills/{old_id}/supersede", json=body)
        assert response.status_code == 200, response.text
        replay = await client.post(f"/api/v1/skills/{old_id}/supersede", json=body)
        assert replay.status_code == 409  # CAS, no duplicate event or state transition.
        async with db.session_factory() as session:
            assert await SkillAccessPolicy().check(
                session, merged_version.id, workspace_id=merged_skill.workspace_id
            )
            with pytest.raises(SkillAccessError, match="skill_source_version_revoked"):
                await SkillAccessPolicy().check(
                    session, UUID(parent["version_id"]), workspace_id=merged_skill.workspace_id
                )
            row = await session.get(SkillRecord, old_id)
            assert (
                row.status is SkillStatus.DEPRECATED
                and row.superseded_by_skill_id == merged_skill.id
            )
    async with db.session_factory() as session:
        assert (await session.get(SkillTrialRecord, old_binding.id)).status == "replaced"
        assert (await session.get(SkillTrialRecord, trial.id)).status == "active"
        events = list(
            await session.scalars(
                select(SkillEventRecord).where(SkillEventRecord.event_type == "skill.superseded")
            )
        )
        assert len(events) == 2 and all(e.payload["actor"] == "local-user" for e in events)
    async with db.session_factory() as session:
        prior = await session.get(SkillRecord, selection_candidate[5].id)
        prior.status = SkillStatus.DISABLED
        await session.commit()
    async with db.session_factory() as session:
        with pytest.raises(SkillAccessError, match="skill_source_version_revoked"):
            await SkillAccessPolicy().check(
                session, merged_version.id, workspace_id=merged_skill.workspace_id
            )
        prior = await session.get(SkillRecord, selection_candidate[5].id)
        prior.status = SkillStatus.DEPRECATED
        await session.commit()
    source = selection_candidate[3]["source"]
    revoked = await client.post(
        f"/api/v1/learning-sources/{source['id']}/revoke",
        json={"expected_status": "valid", "reason": "withdraw original merged provenance"},
    )
    assert revoked.status_code == 200, revoked.text
    async with db.session_factory() as session:
        with pytest.raises(SkillAccessError):
            await SkillAccessPolicy().check(
                session, merged_version.id, workspace_id=merged_skill.workspace_id
            )
