"""Frozen inputs are not completed validation or trial readiness."""

from uuid import UUID

import pytest
from sqlalchemy import func, select

from evoagent.db.models import (
    EvalDatasetRecord,
    LearningRequestRecord,
    LearningSourceRecord,
    MaintenanceJobRecord,
)
from evoagent.db.repositories.base import ConcurrentUpdateError
from evoagent.evals.validators import default_validator_registry
from evoagent.learning.schema import LearningError
from evoagent.learning.validation import PersonalValidationService, ValidationAdmission
from evoagent.skills.canonical import content_hash
from evoagent.trace.artifacts import LocalArtifactStore

pytest_plugins = ("tests.integration.test_personal_trials",)


async def inputs(context, *, review=True):
    client, db, _, state, _, _ = context
    if review:
        response = await client.post(
            f"/api/v1/learning-requests/{state['id']}/review",
            json={
                "action": "acknowledge",
                "expected_lock_version": state["lock_version"],
                "reason": "method reviewed",
            },
        )
        assert response.status_code == 200, response.text
        state = response.json()
    async with db.session_factory() as session:
        parent = await session.get(LearningRequestRecord, UUID(state["id"]))
        source = await session.scalar(
            select(LearningSourceRecord).where(LearningSourceRecord.run_id == parent.origin_run_id)
        )
    payload = ValidationAdmission(
        client_request_id="validate_new_inputs",
        expected_parent_lock_version=state["lock_version"],
        reviewed_source_hash=source.content_hash,
        independence_reason="Reviewed the source; these inputs were not used to extract the method",
        cases=tuple(
            {
                "case_key": key,
                "case_kind": kind,
                "task_family": "data",
                "public_input": {"goal": "Preserve identifiers", "inputs": {"rows": [value]}},
                "criteria": [
                    {
                        "criterion_id": "business",
                        "kind": "user",
                        "description": "Check all identifiers",
                        "expected": {"identifier": value},
                        "business_criterion": True,
                    }
                ],
            }
            for key, kind, value in (
                ("positive_new", "positive", "00201"),
                ("counter_new", "counterexample", "not_a_table"),
            )
        ),
    )
    settings = client._transport.app.state.settings
    service = PersonalValidationService(
        db.session_factory,
        LocalArtifactStore(settings.artifact_root),
        default_validator_registry(),
        learning_enabled=True,
    )
    return service, parent, payload, source


async def test_admission_freezes_scoped_dataset_criteria_and_replay_without_dispatch(
    trial_candidate,
):
    service, parent, payload, _ = await inputs(trial_candidate)
    result = await service.prepare_cases(parent.id, payload)
    replay = await service.prepare_cases(parent.id, payload)
    alias = await service.prepare_cases(
        parent.id, payload.model_copy(update={"client_request_id": "alias_new"})
    )
    assert result.id == replay.id == alias.id
    _, db, _, _, candidate, _ = trial_candidate
    async with db.session_factory() as session:
        row = await session.get(LearningRequestRecord, result.id)
        assert row.request_kind == "validate" and row.candidate_version_id == candidate.id
        assert row.validation_report is None and row.validation_experiment_id is None
        assert row.policy_snapshot["input_review"]["origin"] == "user"
        dataset = await session.get(
            EvalDatasetRecord, UUID(row.frozen_inputs["validation_dataset_id"])
        )
        assert dataset.purpose == "personal_dev" and dataset.status.value == "frozen"
        assert dataset.content_hash == row.frozen_inputs["validation_input_manifest_hash"]
        assert row.source_key.startswith("validate:v1:")
        assert (
            await session.scalar(
                select(func.count())
                .select_from(MaintenanceJobRecord)
                .where(MaintenanceJobRecord.learning_request_id == row.id)
            )
            == 0
        )
        assert await session.scalar(select(func.count()).select_from(EvalDatasetRecord)) == 1


async def test_admission_requires_review_and_source_version(trial_candidate):
    service, parent, payload, _ = await inputs(trial_candidate, review=False)
    with pytest.raises(LearningError, match="candidate_review_required"):
        await service.prepare_cases(parent.id, payload)
    with pytest.raises(LearningError, match="source_review_stale"):
        await service.prepare_cases(
            parent.id, payload.model_copy(update={"reviewed_source_hash": "sha256:" + "0" * 64})
        )


async def test_changed_idempotent_request_rolls_back_new_dataset(trial_candidate):
    service, parent, payload, _ = await inputs(trial_candidate)
    await service.prepare_cases(parent.id, payload)
    changed = ValidationAdmission.model_validate(
        {**payload.model_dump(mode="json"), "independence_reason": "A changed review"}
    )
    with pytest.raises(ConcurrentUpdateError):
        await service.prepare_cases(parent.id, changed)
    async with trial_candidate[1].session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(EvalDatasetRecord)) == 1


async def test_revoked_source_and_disabled_learning_cannot_freeze(trial_candidate):
    service, parent, payload, source = await inputs(trial_candidate)
    service.enabled = False
    with pytest.raises(LearningError, match="learning_disabled"):
        await service.prepare_cases(parent.id, payload)
    service.enabled = True
    async with trial_candidate[1].session_factory() as session:
        row = await session.get(LearningSourceRecord, source.id)
        row.status = "revoked"
        row.revocation_epoch += 1
        await session.commit()
    with pytest.raises(LearningError, match="learning_source_revoked"):
        await service.prepare_cases(parent.id, payload)


async def test_exact_known_input_hash_cannot_be_called_independent(trial_candidate, monkeypatch):
    service, parent, payload, _ = await inputs(trial_candidate)
    from evoagent.learning.sources import PersonalSourceService

    original = PersonalSourceService.read_frozen

    async def with_known_input(self, *args, **kwargs):
        result = await original(self, *args, **kwargs)
        return result.model_copy(
            update={
                "input_refs": ({"hash": content_hash(payload.cases[0].public_input["inputs"])},)
            }
        )

    monkeypatch.setattr(PersonalSourceService, "read_frozen", with_known_input)
    with pytest.raises(LearningError, match="input_reuses_source"):
        await service.prepare_cases(parent.id, payload)


@pytest.mark.parametrize("unsupported", ["fixture", "validator"])
async def test_unregistered_fixture_and_unknown_validator_leave_no_dataset(
    trial_candidate, unsupported
):
    service, parent, payload, _ = await inputs(trial_candidate)
    body = payload.model_dump(mode="json")
    if unsupported == "fixture":
        body["cases"][0]["fixture_id"] = "registered_later"
        code = "fixture_not_registered"
    else:
        body["cases"][0]["criteria"].append(
            {
                "criterion_id": "unknown",
                "kind": "machine",
                "description": "Unavailable judge",
                "expected": True,
                "validator": {"name": "not_registered"},
            }
        )
        code = "validator_not_registered"
    with pytest.raises(LearningError, match=code):
        await service.prepare_cases(parent.id, ValidationAdmission.model_validate(body))
    async with trial_candidate[1].session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(EvalDatasetRecord)) == 0


async def test_current_off_policy_refuses_admission(trial_candidate):
    service, parent, payload, _ = await inputs(trial_candidate)
    from evoagent.db.models import LearningPolicyRecord

    async with trial_candidate[1].session_factory() as session:
        policy = await session.get(LearningPolicyRecord, parent.workspace_id)
        policy.mode = "off"
        await session.commit()
    with pytest.raises(LearningError, match="learning_policy_off"):
        await service.prepare_cases(parent.id, payload)


async def test_registered_fixture_aliases_cannot_claim_independent_inputs(trial_candidate):
    from evoagent.learning.replicas import RegisteredFixture

    service, parent, payload, _ = await inputs(trial_candidate)
    service.fixtures = {
        item.fixture_id: item
        for item in (
            RegisteredFixture("first_alias", (("first.txt", b"same data"),)),
            RegisteredFixture("second_alias", (("second.txt", b"same data"),)),
        )
    }
    body = payload.model_dump(mode="json")
    for case, fixture_id in zip(body["cases"], service.fixtures, strict=True):
        case["fixture_id"] = fixture_id
    with pytest.raises(LearningError, match="fixture_inputs_not_distinct"):
        await service.prepare_cases(parent.id, ValidationAdmission.model_validate(body))
    async with trial_candidate[1].session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(EvalDatasetRecord)) == 0
