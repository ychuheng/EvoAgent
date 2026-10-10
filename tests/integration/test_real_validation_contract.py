"""Real dispatch/accounting wiring with an injected offline provider, never efficacy."""

from uuid import UUID

import pytest
from pydantic import SecretStr
from sqlalchemy import select

from evoagent.config import ProviderName
from evoagent.core.models import FinishReason, Message, MessageRole, ModelResponse, TokenUsage
from evoagent.db.models import (
    DEFAULT_WORKSPACE_ID,
    LearningPolicyRecord,
    LearningRequestRecord,
    LearningSpendReservationRecord,
    RunRecord,
    SpendRecord,
)
from evoagent.learning.budget import LearningBudgetService
from evoagent.memory.maintenance import MaintenanceWorker
from evoagent.providers.mock import MockProvider
from evoagent.trace.artifacts import LocalArtifactStore
from evoagent.workers.bootstrap import ConfiguredTaskHandler
from tests.integration.test_learning_worker import worker
from tests.integration.test_personal_selection_v3 import execute
from tests.integration.test_validation_admission import inputs

pytest_plugins = ("tests.integration.test_personal_selection_v3",)


async def register_host(context):
    client, db, *_ = context
    settings = client._transport.app.state.settings
    settings.personal_validation_real_enabled = True
    settings.provider = ProviderName.OPENAI_COMPATIBLE
    settings.model = "offline-dispatch-fixture"
    settings.base_url = "https://never-called.example.test/v1"
    settings.api_key = SecretStr("offline-fixture-credential")
    settings.budget_total_limit_micros = 10000000
    settings.budget_task_limit_micros = 1000000
    settings.budget_input_price_micros_per_million = 1000000
    settings.budget_output_price_micros_per_million = 1000000
    settings.model_request_max_output_tokens = 4096
    async with db.session_factory() as session:
        policy = await session.get(LearningPolicyRecord, DEFAULT_WORKSPACE_ID)
        policy.daily_limit_micros = 1000000
        policy.request_limit_micros = 1000000
        await session.commit()
    return settings


async def admission(context):
    _, parent, payload, _ = await inputs(context)
    body = payload.model_dump(mode="json")
    body["execution_profile_id"] = "host-real-v1"
    for case, fixture in zip(body["cases"], ("data_identifiers", "data_free_text"), strict=True):
        case["fixture_id"] = fixture
        case["task_family"] = "data"
        case["public_input"] = {"goal": "Read the registered file and verify output"}
    return parent, body


async def test_real_validation_uses_task_bound_learning_ledger_without_double_charging(
    selection_candidate, monkeypatch, tmp_path
):
    client, db, *_ = selection_candidate
    settings = await register_host(selection_candidate)
    parent, body = await admission(selection_candidate)
    response = await client.post(f"/api/v1/learning-requests/{parent.id}/validations", json=body)
    assert response.status_code == 202, response.text
    prepared = response.json()
    assert prepared["policy_snapshot"]["provider"] == "openai_compatible"
    assert prepared["policy_snapshot"]["execution_profile_hash"]
    assert "offline-fixture-credential" not in response.text
    handler = worker(db, settings).handlers["learning_validate"]
    handler.budget = LearningBudgetService(db.session_factory, settings)
    learning = MaintenanceWorker(
        db.session_factory,
        LocalArtifactStore(settings.artifact_root),
        handlers={"learning_validate": handler, "learning_validation_completed": handler},
        allowed_kinds={"learning_validate", "learning_validation_completed"},
    )
    response = await client.post(
        f"/api/v1/learning-requests/{prepared['id']}/validation-start",
        json={"expected_lock_version": prepared["lock_version"]},
    )
    assert response.status_code == 202, response.text
    assert await learning.run_once()
    async with db.session_factory() as session:
        request = await session.get(LearningRequestRecord, UUID(prepared["id"]))
        assert request.stage == "waiting_validation", request.error_code
    providers = []

    def offline(_self, run_id):
        provider = MockProvider(
            [
                ModelResponse(
                    message=Message(role=MessageRole.ASSISTANT, content="injected fixture output"),
                    finish_reason=FinishReason.STOP,
                    usage=TokenUsage(input_tokens=2, output_tokens=1, total_tokens=3),
                )
            ]
        )
        providers.append(provider)
        return provider

    monkeypatch.setattr(ConfiguredTaskHandler, "_provider", offline)
    await execute(db, settings)
    assert await learning.run_once()
    assert await learning.run_once()
    async with db.session_factory() as session:
        request = await session.get(LearningRequestRecord, UUID(prepared["id"]))
        assert request.stage == "validation_review", request.error_code
        report = request.validation_report
        assert report["cost"] == {
            "provider": "openai_compatible",
            "paid_calls": 4,
            "usage_complete": True,
            "known_spent_micros": 12,
        }
        assert report["adoption_verification"] == "passed"
        assert report["business_verification"] == "pending" and not report["trial_eligible"]
        reservations = list(
            await session.scalars(
                select(LearningSpendReservationRecord).where(
                    LearningSpendReservationRecord.request_id == request.id
                )
            )
        )
        assert len(reservations) == 4
        assert all(row.dispatcher_kind == "task" and row.task_epoch for row in reservations)
        assert all(row.status == "settled" and row.request_body_hash for row in reservations)
        spends = list(await session.scalars(select(SpendRecord)))
        assert len(spends) == 4
        assert all(row.purpose == "skill_validation" and row.reservation_id for row in spends)
    assert len(providers) == 4 and all(len(provider.requests) == 1 for provider in providers)
    # The following human claims and provider responses are fixtures. They
    # exercise admission -> trial -> ordinary use, never assert real quality.
    settings.personal_trial_enabled = True
    response = await client.post(
        f"/api/v1/learning-requests/{request.id}/judgments",
        json={
            "client_request_id": "synthetic-judgment",
            "expected_lock_version": request.lock_version,
            "expected_report_hash": request.validation_report_hash,
            "judgments": [
                {
                    "eval_run_id": next(
                        ref["id"] for ref in item["evidence_refs"] if ref["type"] == "eval_run"
                    ),
                    "criterion_id": item["criterion_id"],
                    "verdict": "pass",
                    "observed": {"fixture": True},
                    "reason": "synthetic user judgment for wiring test",
                }
                for item in report["items"]
            ],
        },
    )
    assert response.status_code == 201, response.text
    assert response.json()["trial_eligible"]
    version_id = request.candidate_version_id
    ready = await client.get(
        f"/api/v1/skill-versions/{version_id}/trial-readiness",
        params={"validation_request_id": str(request.id)},
    )
    assert ready.status_code == 200 and ready.json()["ready"], ready.text
    # Even a self-consistent report hash cannot replace persisted payment evidence.
    from copy import deepcopy

    from evoagent.skills.canonical import content_hash

    async with db.session_factory() as session:
        current = await session.get(LearningRequestRecord, request.id)
        saved = deepcopy(current.validation_report)
        forged = deepcopy(saved)
        forged["cost"]["paid_calls"] += 1
        current.validation_report = forged
        current.validation_report_hash = content_hash(forged)
        await session.commit()
    rejected = await client.get(
        f"/api/v1/skill-versions/{version_id}/trial-readiness",
        params={"validation_request_id": str(request.id)},
    )
    assert not rejected.json()["ready"]
    assert "real_validation_payment_invalid" in rejected.json()["reasons"]
    async with db.session_factory() as session:
        current = await session.get(LearningRequestRecord, request.id)
        current.validation_report = saved
        current.validation_report_hash = content_hash(saved)
        await session.commit()
    response = await client.post(
        f"/api/v1/skill-versions/{version_id}/trial",
        json={
            "workspace_id": str(request.workspace_id),
            "validation_request_id": str(request.id),
            "expected_lock_version": ready.json()["skill_lock_version"],
            "reason": "synthetic trial admission contract",
        },
    )
    assert response.status_code == 201, response.text
    trial = response.json()

    from evoagent.projects.service import ProjectService
    from evoagent.tasks.lease import JobLeaseManager
    from evoagent.tasks.service import TaskService

    root = tmp_path / "ordinary-project"
    root.mkdir()
    (root / "input.csv").write_text("identifier\n00042\n", encoding="utf8")
    project = await ProjectService(db.session_factory).register(path=str(root))
    tasks = TaskService(db.session_factory)
    chat = await tasks.create_session("ordinary trial user")
    aggregate = await tasks.create_task(
        session_id=chat.id,
        goal="核验 验证 检查",
        family="data",
        provider=settings.provider.value,
        model=settings.model,
        project_id=project.id,
        input_paths=["input.csv"],
    )
    lease = await JobLeaseManager(db.session_factory, lease_seconds=60).claim_next("ordinary-trial")
    assert lease.run_id == aggregate.run.id
    result = await ConfiguredTaskHandler(settings, db).handle(lease)
    assert str(result.status) == "completed", result.error_code
    await JobLeaseManager(db.session_factory, lease_seconds=120).finalize(lease, result)
    async with db.session_factory() as session:
        ordinary = await session.get(RunRecord, lease.run_id)
        selection = ordinary.config_snapshot["selected_skills"][0]
        assert selection["origin"] == "trial" and selection["trial_id"] == trial["id"]
    assert len(providers) == 5 and len(providers[-1].requests) == 1


async def test_profile_change_before_start_blocks_dispatch(selection_candidate):
    client, db, *_ = selection_candidate
    settings = await register_host(selection_candidate)
    parent, body = await admission(selection_candidate)
    response = await client.post(f"/api/v1/learning-requests/{parent.id}/validations", json=body)
    assert response.status_code == 202, response.text
    child = response.json()
    settings.model = "changed-fixture-model"
    response = await client.post(
        f"/api/v1/learning-requests/{child['id']}/validation-start",
        json={"expected_lock_version": child["lock_version"]},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "validation_execution_profile_changed"
    async with db.session_factory() as session:
        request = await session.get(LearningRequestRecord, UUID(child["id"]))
        assert request.stage == "task_validate" and request.status == "queued"
        assert not list(await session.scalars(select(LearningSpendReservationRecord)))


@pytest.mark.parametrize("approval", [None, 0])
async def test_workspace_numeric_approval_required_before_real_dispatch(
    selection_candidate, approval
):
    client, db, *_ = selection_candidate
    await register_host(selection_candidate)
    parent, body = await admission(selection_candidate)
    async with db.session_factory() as session:
        policy = await session.get(LearningPolicyRecord, DEFAULT_WORKSPACE_ID)
        policy.daily_limit_micros = approval
        await session.commit()
    response = await client.post(f"/api/v1/learning-requests/{parent.id}/validations", json=body)
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "learning_waiting_budget"
    async with db.session_factory() as session:
        assert not list(await session.scalars(select(LearningSpendReservationRecord)))
        assert not list(
            await session.scalars(
                select(LearningRequestRecord).where(
                    LearningRequestRecord.request_kind == "validate"
                )
            )
        )


async def test_real_call_cap_counts_reserved_and_settled_calls_without_resending(
    selection_candidate,
):
    from evoagent.learning.schema import LearningError
    from evoagent.learning.task_budget_guard import ValidationTaskBudgetGuard
    from evoagent.learning.validation_guard import PersonalValidationRunGuard
    from evoagent.skills.canonical import content_hash
    from evoagent.tasks.lease import JobLeaseManager

    client, db, *_ = selection_candidate
    settings = await register_host(selection_candidate)
    settings.max_iterations = 1
    parent, body = await admission(selection_candidate)
    prepared = (
        await client.post(f"/api/v1/learning-requests/{parent.id}/validations", json=body)
    ).json()
    assert prepared["policy_snapshot"]["maximum_model_calls"] == 4
    handler = worker(db, settings).handlers["learning_validate"]
    handler.budget = LearningBudgetService(db.session_factory, settings)
    learning = MaintenanceWorker(
        db.session_factory,
        LocalArtifactStore(settings.artifact_root),
        handlers={"learning_validate": handler},
        allowed_kinds={"learning_validate"},
    )
    response = await client.post(
        f"/api/v1/learning-requests/{prepared['id']}/validation-start",
        json={"expected_lock_version": prepared["lock_version"]},
    )
    assert response.status_code == 202, response.text
    assert await learning.run_once()
    lease = await JobLeaseManager(db.session_factory, lease_seconds=300).claim_next("cap-test")
    validation = await PersonalValidationRunGuard.for_run(
        db.session_factory,
        LocalArtifactStore(settings.artifact_root),
        lease.run_id,
        learning_enabled=True,
        settings=settings,
    )
    guard = ValidationTaskBudgetGuard(lease, validation)
    budget = LearningBudgetService(db.session_factory, settings)
    request_id = UUID(prepared["id"])
    first = None
    for index in range(4):
        identity = await budget.reserve(
            request_id,
            f"cap-call-{index}",
            10,
            guard=guard,
            request_body_hash=content_hash({"call": index}),
        )
        if index == 0:
            first = identity
        if index < 3:
            await budget.mark_dispatched(identity, guard=guard)
            await budget.settle(
                identity,
                TokenUsage(input_tokens=2, output_tokens=1, total_tokens=3),
                provider=settings.provider.value,
                model=settings.model,
            )
    with pytest.raises(LearningError, match="validation_call_bound_exceeded"):
        await budget.reserve(
            request_id, "cap-fifth", 10, guard=guard, request_body_hash=content_hash({"call": 4})
        )
    with pytest.raises(LearningError, match="learning_call_already_sent"):
        await budget.reserve(
            request_id, "cap-call-0", 10, guard=guard, request_body_hash=content_hash({"call": 0})
        )
    async with db.session_factory() as session:
        rows = list(await session.scalars(select(LearningSpendReservationRecord)))
        assert len(rows) == 4 and first in {row.id for row in rows}
