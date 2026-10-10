"""Merge keeps frozen lineage and produces review-only candidates without paid work."""

from copy import deepcopy
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select

from evoagent.db.models import (
    LearningRequestRecord,
    MaintenanceJobRecord,
    RunRecord,
    SkillRecord,
    SkillSourceRecord,
    SkillTrialRecord,
    SkillVersionRecord,
    TaskRecord,
)
from evoagent.skills.access import SkillAccessError, SkillAccessPolicy
from evoagent.skills.canonical import content_hash
from evoagent.skills.lifecycle import SkillStatus, SkillVersionStatus

pytest_plugins = ("tests.integration.test_personal_trials",)


async def merge_body(context):
    _, db, _, _, original, skill = context
    async with db.session_factory() as session:
        source = await session.scalar(
            select(SkillSourceRecord).where(SkillSourceRecord.skill_version_id == original.id)
        )
        other = SkillRecord(
            workspace_id=skill.workspace_id,
            slug="second_method",
            name="second method",
            description="independent method fixture",
        )
        session.add(other)
        await session.flush()
        definition = deepcopy(original.definition)
        definition["name"] = other.slug
        version = SkillVersionRecord(
            skill_id=other.id,
            version=1,
            schema_version=original.schema_version,
            definition=definition,
            content_hash=content_hash(definition),
            extraction_key=content_hash({"fixture": str(uuid4())}),
            lifecycle_status=SkillVersionStatus.DRAFT,
        )
        session.add(version)
        await session.flush()
        session.add(
            SkillSourceRecord(
                skill_version_id=version.id,
                source_run_id=source.source_run_id,
                source_kind=source.source_kind,
                learning_source_id=source.learning_source_id,
                trace_artifact_id=source.trace_artifact_id,
                source_trace_hash=source.source_trace_hash,
            )
        )
        await session.commit()
    definition = deepcopy(original.definition)
    definition["name"] = "combined_method"
    definition["description"] = "Manually reviewed combination of two methods"
    return {
        "workspace_id": str(skill.workspace_id),
        "project_id": None,
        "parents": [
            {
                "skill_id": str(s.id),
                "version_id": str(v.id),
                "content_hash": v.content_hash,
                "lock_version": s.lock_version,
            }
            for s, v in ((skill, original), (other, version))
        ],
        "definition": definition,
        "client_request_id": "explicit-merge",
        "reason": "combine repeated steps",
    }


async def test_merge_is_idempotent_review_only_and_keeps_both_parent_gates(trial_candidate):
    client, db, _, _, _, skill = trial_candidate
    body = await merge_body(trial_candidate)
    result = await client.post("/api/v1/skills/merge-proposals", json=body)
    assert result.status_code == 202, result.text
    state = result.json()
    assert state["status"] == "ready_for_review"
    assert not state["validation_report"]["trial_eligible"]
    assert state["validation_report"]["task_validation"]["status"] == "not_run"
    repeated = await client.post("/api/v1/skills/merge-proposals", json=body)
    assert repeated.status_code == 202, repeated.text
    assert repeated.json()["id"] == state["id"]
    async with db.session_factory() as session:
        candidate = await session.get(SkillVersionRecord, UUID(state["candidate_version_id"]))
        assert candidate.parent_version_id is None
        assert candidate.lifecycle_status is SkillVersionStatus.DRAFT
        merged = await session.get(SkillRecord, candidate.skill_id)
        assert merged.active_version_id is None
        request = await session.get(LearningRequestRecord, UUID(state["id"]))
        assert {p["version_id"] for p in request.policy_snapshot["lineage"]} == {
            p["version_id"] for p in body["parents"]
        }
        assert not await session.scalar(
            select(func.count())
            .select_from(MaintenanceJobRecord)
            .where(MaintenanceJobRecord.learning_request_id == request.id)
        )
        assert not await session.scalar(select(func.count()).select_from(SkillTrialRecord))
        assert await SkillAccessPolicy().check(
            session, candidate.id, workspace_id=skill.workspace_id
        )
        secondary = await session.get(SkillRecord, UUID(body["parents"][1]["skill_id"]))
        secondary.status = SkillStatus.DISABLED
        await session.commit()
    async with db.session_factory() as session:
        with pytest.raises(SkillAccessError, match="skill_source_version_revoked"):
            await SkillAccessPolicy().check(session, candidate.id, workspace_id=skill.workspace_id)
    review = await client.post(
        f"/api/v1/learning-requests/{state['id']}/review",
        json={
            "expected_lock_version": state["lock_version"],
            "action": "acknowledge",
            "reason": "review",
        },
    )
    assert review.status_code == 422, review.text
    assert "candidate_source_graph_invalid" in review.text


@pytest.mark.parametrize("change", ["hash", "lock", "duplicate", "scope", "secret"])
async def test_merge_rejects_invalid_identity_without_candidate(trial_candidate, change):
    client, db, _, _, _, _ = trial_candidate
    body = await merge_body(trial_candidate)
    if change == "hash":
        body["parents"][1]["content_hash"] = "sha256:" + "0" * 64
    elif change == "lock":
        body["parents"][1]["lock_version"] += 1
    elif change == "duplicate":
        body["parents"][1] = deepcopy(body["parents"][0])
    elif change == "scope":
        body["project_id"] = str(uuid4())
    else:
        body["reason"] = "password=long-secret-value-not-for-reflection"
    response = await client.post("/api/v1/skills/merge-proposals", json=body)
    assert response.status_code in {409, 422}, response.text
    assert "long-secret-value" not in response.text
    async with db.session_factory() as session:
        assert not await session.scalar(
            select(SkillRecord.id).where(SkillRecord.slug == "combined_method")
        )
        assert not await session.scalar(
            select(LearningRequestRecord.id).where(LearningRequestRecord.trigger == "merge")
        )


async def test_merge_review_does_not_adopt_or_deprecate_parents(trial_candidate):
    client, db, _, _, _, _ = trial_candidate
    body = await merge_body(trial_candidate)
    result = await client.post("/api/v1/skills/merge-proposals", json=body)
    assert result.status_code == 202, result.text
    state = result.json()
    reviewed = await client.post(
        f"/api/v1/learning-requests/{state['id']}/review",
        json={
            "expected_lock_version": state["lock_version"],
            "action": "acknowledge",
            "reason": "checked combination",
        },
    )
    assert reviewed.status_code == 200, reviewed.text
    assert reviewed.json()["status"] == "completed"
    async with db.session_factory() as session:
        candidate = await session.get(SkillVersionRecord, UUID(state["candidate_version_id"]))
        owner = await session.get(SkillRecord, candidate.skill_id)
        assert owner.active_version_id is None
        assert candidate.lifecycle_status is SkillVersionStatus.DRAFT
        for ref in body["parents"]:
            parent = await session.get(SkillRecord, UUID(ref["skill_id"]))
            assert parent.status is SkillStatus.ENABLED
            assert parent.superseded_by_skill_id is None
        assert not await session.scalar(select(func.count()).select_from(SkillTrialRecord))


async def test_rejected_merge_cannot_be_injected_as_valid_lineage(trial_candidate):
    client, db, _, _, _, skill = trial_candidate
    body = await merge_body(trial_candidate)
    result = await client.post("/api/v1/skills/merge-proposals", json=body)
    assert result.status_code == 202, result.text
    state = result.json()
    rejected = await client.post(
        f"/api/v1/learning-requests/{state['id']}/review",
        json={
            "expected_lock_version": state["lock_version"],
            "action": "reject",
            "reason": "combination unsuitable",
        },
    )
    assert rejected.status_code == 200
    async with db.session_factory() as session:
        with pytest.raises(SkillAccessError, match="skill_merge_lineage_invalid"):
            await SkillAccessPolicy().check(
                session, UUID(state["candidate_version_id"]), workspace_id=skill.workspace_id
            )


async def test_merge_unions_independent_origins_and_secondary_revocation(
    trial_candidate, learning_api
):
    from evoagent.skills.extraction import MockCandidateGenerator
    from evoagent.skills.schema import SkillDefinition
    from tests.integration.test_learning_worker import worker

    client, db, original_run, _, original, skill = trial_candidate
    settings = learning_api[3]
    body = await merge_body(trial_candidate)
    async with db.session_factory() as session:
        prior = await session.get(RunRecord, original_run)
        task = await session.get(TaskRecord, prior.task_id)
        second_task = TaskRecord(
            session_id=task.session_id,
            goal="new independent source",
            status="completed",
            frozen_inputs={
                "files": [
                    {
                        "sha256": content_hash({"rows": ["00201"]})[7:],
                        "size_bytes": 10,
                        "kind": "fixture",
                    }
                ]
            },
        )
        session.add(second_task)
        await session.flush()
        second_run = RunRecord(
            id=UUID(int=original_run.int + 1),
            task_id=second_task.id,
            provider="mock",
            model="mock",
            status="completed",
            data_role="personal",
        )
        session.add(second_run)
        await session.commit()
    response = await client.post(
        f"/api/v1/runs/{second_run.id}/learning-requests",
        json={"client_request_id": "independent-merge-origin"},
    )
    assert response.status_code == 202, response.text
    definition = deepcopy(original.definition)
    definition["name"] = "independent_method"
    runner = worker(
        db, settings, MockCandidateGenerator(SkillDefinition.model_validate(definition))
    )
    for _ in range(3):
        assert await runner.run_once()
    state = (await client.get(f"/api/v1/learning-requests/{response.json()['id']}")).json()
    assert state["status"] == "ready_for_review", state
    async with db.session_factory() as session:
        second = await session.get(SkillVersionRecord, UUID(state["candidate_version_id"]))
        owner = await session.get(SkillRecord, second.skill_id)
        body["parents"][1] = {
            "skill_id": str(owner.id),
            "version_id": str(second.id),
            "content_hash": second.content_hash,
            "lock_version": owner.lock_version,
        }
    merged = await client.post("/api/v1/skills/merge-proposals", json=body)
    assert merged.status_code == 202, merged.text
    async with db.session_factory() as session:
        links = list(
            await session.scalars(
                select(SkillSourceRecord).where(
                    SkillSourceRecord.skill_version_id
                    == UUID(merged.json()["candidate_version_id"])
                )
            )
        )
        assert {link.source_run_id for link in links} == {original_run, second_run.id}
    # Independent validation must reject inputs known to either parent,
    # regardless of which source was selected as the request's primary origin.
    from evoagent.evals.validators import default_validator_registry
    from evoagent.learning.validation import PersonalValidationService
    from evoagent.trace.artifacts import LocalArtifactStore
    from tests.integration.test_validation_admission import inputs

    reviewed = await client.post(
        f"/api/v1/learning-requests/{merged.json()['id']}/review",
        json={
            "action": "acknowledge",
            "expected_lock_version": merged.json()["lock_version"],
            "reason": "reviewed both origins",
        },
    )
    assert reviewed.status_code == 200, reviewed.text
    _, _, payload, _ = await inputs(trial_candidate)
    source = (await client.get(f"/api/v1/learning-requests/{merged.json()['id']}")).json()["source"]
    assert source["run_id"] == str(
        original_run
    )  # The reused input belongs to the non-primary source.
    payload = payload.model_copy(
        update={
            "expected_parent_lock_version": reviewed.json()["lock_version"],
            "reviewed_source_hash": source["content_hash"],
            "client_request_id": "merge-independent-test",
        }
    )
    service = PersonalValidationService(
        db.session_factory,
        LocalArtifactStore(settings.artifact_root),
        default_validator_registry(),
        learning_enabled=True,
    )
    with pytest.raises(ValueError, match="validation_input_reuses_source"):
        await service.prepare_cases(UUID(merged.json()["id"]), payload)
    revoked = await client.post(
        f"/api/v1/learning-sources/{state['source']['id']}/revoke",
        json={"expected_status": "valid", "reason": "withdraw second origin"},
    )
    assert revoked.status_code == 200, revoked.text
    async with db.session_factory() as session:
        with pytest.raises(SkillAccessError):
            await SkillAccessPolicy().check(
                session,
                UUID(merged.json()["candidate_version_id"]),
                workspace_id=skill.workspace_id,
            )
