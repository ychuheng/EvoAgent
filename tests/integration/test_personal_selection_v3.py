"""Actual offline v3 injection and negative decisions, not model-quality evidence."""

import asyncio
from uuid import UUID

import pytest
from sqlalchemy import select

from evoagent.core.models import FinishReason, Message, MessageRole, ModelResponse
from evoagent.db.models import (
    EvalCaseRecord,
    EvalRunRecord,
    LearningRequestRecord,
    RunRecord,
    RunSkillSelectionRecord,
    SkillRecord,
    SkillVersionRecord,
)
from evoagent.evals.coordinator import EvalCoordinator
from evoagent.evals.validators import default_validator_registry
from evoagent.learning.demo import PersonalDemoGenerator
from evoagent.providers.mock import MockProvider
from evoagent.runtime.run_config import RunConfigSnapshot
from evoagent.skills.canonical import content_hash
from evoagent.skills.rendering import SkillContextRenderer
from evoagent.skills.schema import SkillDefinition
from evoagent.skills.selection import SkillSelector
from evoagent.tasks.lease import JobLeaseManager
from evoagent.workers.bootstrap import ConfiguredTaskHandler
from evoagent.workers.main import JobWorker
from tests.integration.test_learning_worker import submit, worker
from tests.integration.test_personal_validation_replicas import prepare

pytest_plugins = ("tests.integration.test_learning_api",)


class DataGenerator(PersonalDemoGenerator):
    async def generate(self, sources, *, context=None):
        data = (await super().generate(sources, context=context)).model_dump(mode="json")
        data["applicability"] = {
            "task_families": ["data"],
            "file_types": ["csv"],
            "required_facts": [
                {"key": "input.exists", "value": True},
                {"key": "project.available", "value": True},
            ],
        }
        return SkillDefinition.model_validate(data)


@pytest.fixture
async def selection_candidate(learning_api):
    client, db, run_id, settings = learning_api
    request = await submit(client, run_id)
    runner = worker(db, settings, DataGenerator())
    for _ in range(3):
        assert await runner.run_once()
    state = (await client.get(f"/api/v1/learning-requests/{request['id']}")).json()
    async with db.session_factory() as session:
        version = await session.get(SkillVersionRecord, UUID(state["candidate_version_id"]))
        skill = await session.get(SkillRecord, version.skill_id)
    return client, db, run_id, state, version, skill


def scripted_providers(monkeypatch):
    providers = {}

    def provider(_self, run_id):
        result = MockProvider(
            [
                ModelResponse(
                    message=Message(role=MessageRole.ASSISTANT, content="offline fixture complete"),
                    finish_reason=FinishReason.STOP,
                )
            ]
        )
        providers[run_id] = result
        return result

    monkeypatch.setattr(ConfiguredTaskHandler, "_provider", provider)
    return providers


async def execute(db, settings):
    tasks = JobWorker(
        worker_id="v3-validation",
        lease_manager=JobLeaseManager(db.session_factory, lease_seconds=60),
        handler=ConfiguredTaskHandler(settings, db),
        heartbeat_seconds=10,
        poll_seconds=0.01,
    )
    coordinator = EvalCoordinator(db.session_factory, default_validator_registry())
    lease = await coordinator.claim_next("v3-eval")
    assert lease is not None
    for _ in range(8):
        assert await tasks.run_once()
        if await coordinator.run_once(lease):
            return
    raise AssertionError("paired validation did not complete")


async def test_actual_v3_positive_injection_and_counterexample_non_adoption(
    selection_candidate, monkeypatch
):
    db, settings, request, learning = await prepare(selection_candidate)
    providers = scripted_providers(monkeypatch)
    original = SkillSelector.resolve_pinned
    restores = []

    async def resolve_and_restore(self, task, run):
        first = await original(self, task, run)
        with monkeypatch.context() as patch:

            def unavailable(*_args, **_kwargs):
                raise AssertionError("restore cannot rerender a frozen method")

            patch.setattr(SkillContextRenderer, "render", unavailable)
            second = await original(self, task, run)
        assert second == first
        restores.append(run.id)
        return first

    monkeypatch.setattr(SkillSelector, "resolve_pinned", resolve_and_restore)
    await execute(db, settings)
    async with db.session_factory() as session:
        rows = list(
            await session.scalars(
                select(EvalRunRecord).where(
                    EvalRunRecord.experiment_id == request.validation_experiment_id
                )
            )
        )
        assert len(rows) == len(restores) == 4
        pairs = {}
        for row in rows:
            run = await session.get(RunRecord, row.run_id)
            case = await session.get(EvalCaseRecord, row.eval_case_id)
            config = RunConfigSnapshot.model_validate(run.config_snapshot)
            assert config.schema_version == 3
            pairs.setdefault(case.case_key, {})[row.arm] = config
            selected = await session.scalar(
                select(RunSkillSelectionRecord).where(RunSkillSelectionRecord.run_id == run.id)
            )
            applied = row.arm == "treatment" and case.case_key == "positive_new"
            assert bool(config.selected_skills) is applied
            if config.selected_skills:
                assert row.arm == "treatment"
                assert case.case_key == "positive_new"
                assert selected.origin == "pinned"
                assert selected.content_hash == config.skill_content_hash
                assert selected.rendered_hash == config.skill_context_hash
                assert config.selected_skills[0].scope.workspace_id == request.workspace_id
                assert any(
                    f"Skill：{selection_candidate[4].definition['name']}" in (message.content or "")
                    for message in providers[run.id].requests[0].messages
                )
            else:
                assert selected is None
            if row.arm == "treatment":
                assert config.under_test_skill_version_id == request.candidate_version_id
            else:
                assert config.under_test_skill_version_id is None and not config.selected_skills
        assert sum(bool(pair["treatment"].selected_skills) for pair in pairs.values()) == 1
        assert all(pair["control"].comparable_with(pair["treatment"]) for pair in pairs.values())
    assert await learning.run_once()
    assert await learning.run_once()
    async with db.session_factory() as session:
        reviewed = await session.get(LearningRequestRecord, request.id)
        assert reviewed.stage == "validation_review", reviewed.error_code
        report = reviewed.validation_report
        assert report["schema_version"] == 2
        assert report["adoption_verification"] == "passed"
        assert report["trial_eligible"] is False
        assert all(item["actual_selection"]["verified"] for item in report["items"])


async def test_refusal_scan_does_not_hold_fencing_lock(selection_candidate, monkeypatch):
    db, settings, request, _ = await prepare(selection_candidate)
    scripted_providers(monkeypatch)
    monkeypatch.setattr(SkillContextRenderer, "render", lambda *_: "password: fixture-only-value")

    async def until_refused():
        tasks = JobWorker(
            worker_id="refusal-test",
            lease_manager=JobLeaseManager(db.session_factory, lease_seconds=60),
            handler=ConfiguredTaskHandler(settings, db),
            heartbeat_seconds=10,
            poll_seconds=0.01,
        )
        coordinator = EvalCoordinator(db.session_factory, default_validator_registry())
        lease = await coordinator.claim_next("refusal-eval")
        for _ in range(4):
            assert await tasks.run_once()
            async with db.session_factory() as session:
                if await session.scalar(
                    select(RunRecord.id).where(RunRecord.error_code == "context_source_blocked")
                ):
                    return
            await coordinator.run_once(lease)
        raise AssertionError("expected refusal not reached")

    await asyncio.wait_for(until_refused(), timeout=20)
    async with db.session_factory() as session:
        rows = list(
            await session.scalars(
                select(RunRecord)
                .join(EvalRunRecord, EvalRunRecord.run_id == RunRecord.id)
                .where(EvalRunRecord.experiment_id == request.validation_experiment_id)
            )
        )
        assert any(run.error_code == "context_source_blocked" for run in rows)
        assert not list(await session.scalars(select(RunSkillSelectionRecord)))


async def test_legacy_prepared_request_keeps_legacy_runtime(selection_candidate, monkeypatch):
    from copy import deepcopy

    from evoagent.db.models import LearningRequestRecord
    from evoagent.learning.repository import LearningRepository
    from evoagent.memory.maintenance import MaintenanceWorker
    from evoagent.trace.artifacts import LocalArtifactStore
    from tests.integration.test_validation_admission import inputs

    service, parent, payload, _ = await inputs(selection_candidate)
    prepared = await service.prepare_cases(parent.id, payload)
    _, db, _, _, _, _ = selection_candidate
    settings = selection_candidate[0]._transport.app.state.settings
    async with db.session_factory() as session:
        row = await session.get(LearningRequestRecord, prepared.id)
        policy = deepcopy(row.policy_snapshot)
        policy.pop("selection_contract_version")
        frozen = deepcopy(row.frozen_inputs)
        frozen["validation_policy_hash"] = content_hash(policy)
        # Isolated historical fixture only: current API cannot change frozen policies.
        await session.execute(
            LearningRequestRecord.__table__.update()
            .where(LearningRequestRecord.id == row.id)
            .values(
                policy_snapshot=policy,
                policy_hash=content_hash(policy),
                frozen_inputs=frozen,
                source_key=LearningRepository.build_source_key("validate", frozen),
            )
        )
        await session.commit()
    handler = worker(db, settings).handlers["learning_validate"]
    learning = MaintenanceWorker(
        db.session_factory,
        LocalArtifactStore(settings.artifact_root),
        handlers={"learning_validate": handler, "learning_validation_completed": handler},
        allowed_kinds={"learning_validate", "learning_validation_completed"},
    )
    await service.start(prepared.id, prepared.lock_version)
    assert await learning.run_once()
    scripted_providers(monkeypatch)
    await execute(db, settings)
    async with db.session_factory() as session:
        rows = list(
            await session.scalars(
                select(RunRecord).join(EvalRunRecord, EvalRunRecord.run_id == RunRecord.id)
            )
        )
        assert len(rows) == 4
        assert all(run.config_snapshot["schema_version"] == 2 for run in rows)
        assert all("selector_version" not in run.config_snapshot for run in rows)


async def test_source_revocation_during_scan_prevents_binding_and_model_call(
    selection_candidate, monkeypatch
):
    from evoagent.db.models import LearningSourceRecord

    db, settings, request, _ = await prepare(selection_candidate)
    providers = scripted_providers(monkeypatch)
    original = SkillSelector._verify_text

    async def revoke_after_scan(self, text, run_id, version_id):
        await original(self, text, run_id, version_id)
        # Independent transaction: scanner must not hold the fencing lock.
        async with db.session_factory() as session:
            source = await session.get(
                LearningSourceRecord, UUID(request.frozen_inputs["source_id"])
            )
            source.status = "revoked"
            source.revocation_epoch += 1
            await session.commit()

    monkeypatch.setattr(SkillSelector, "_verify_text", revoke_after_scan)
    tasks = JobWorker(
        worker_id="revocation-test",
        lease_manager=JobLeaseManager(db.session_factory, lease_seconds=60),
        handler=ConfiguredTaskHandler(settings, db),
        heartbeat_seconds=10,
        poll_seconds=0.01,
    )
    coordinator = EvalCoordinator(db.session_factory, default_validator_registry())
    lease = await coordinator.claim_next("revocation-eval")
    assert lease is not None
    for _ in range(4):
        assert await asyncio.wait_for(tasks.run_once(), timeout=20)
        async with db.session_factory() as session:
            refused = await session.scalar(
                select(RunRecord).where(RunRecord.error_code == "skill_source_revoked")
            )
            if refused is not None:
                assert not providers[refused.id].requests
                assert not list(await session.scalars(select(RunSkillSelectionRecord)))
                return
        await coordinator.run_once(lease)
    raise AssertionError("revoked source was not refused before model dispatch")
