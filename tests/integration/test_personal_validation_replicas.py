"""Real file tools operate on distinct replicas; Mock is never adoption proof."""

import hashlib
import io
import json
import zipfile

import pytest
from sqlalchemy import select

from evoagent.core.models import FinishReason, Message, MessageRole, ModelResponse, ToolCall
from evoagent.db.models import ArtifactRecord, LearningRequestRecord, ValidationReplicaBindingRecord
from evoagent.evals.coordinator import EvalCoordinator
from evoagent.evals.validators import default_validator_registry
from evoagent.learning.replica_bindings import replica_factory
from evoagent.learning.replicas import default_personal_fixtures
from evoagent.learning.validation_guard import PersonalValidationRunGuard
from evoagent.memory.maintenance import MaintenanceWorker
from evoagent.memory.schema import MemoryError
from evoagent.providers.mock import MockProvider
from evoagent.tasks.lease import JobLeaseManager
from evoagent.trace.artifacts import ArtifactService, LocalArtifactStore
from evoagent.workers.bootstrap import ConfiguredTaskHandler
from evoagent.workers.main import JobWorker
from tests.integration.test_learning_worker import worker
from tests.integration.test_validation_admission import inputs

pytest_plugins = ("tests.integration.test_personal_trials",)


async def prepare(context, fixture_ids=("data_identifiers", "data_free_text")):
    _, parent, payload, _ = await inputs(context)
    body = payload.model_dump(mode="json")
    for case, fixture_id in zip(body["cases"], fixture_ids, strict=True):
        case["fixture_id"] = fixture_id
        case["task_family"] = fixture_id.split("_", 1)[0]
        case["public_input"] = {"goal": "Read the registered file and append a verification note"}
    client = context[0]
    catalog = await client.get("/api/v1/personal-validation-fixtures")
    assert catalog.status_code == 200
    assert set(fixture_ids) <= {item["fixture_id"] for item in catalog.json()}
    response = await client.post(f"/api/v1/learning-requests/{parent.id}/validations", json=body)
    assert response.status_code == 202, response.text
    prepared = response.json()
    db = context[1]
    settings = context[0]._transport.app.state.settings
    handler = worker(db, settings).handlers["learning_validate"]
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
        from uuid import UUID

        request = await session.get(LearningRequestRecord, UUID(prepared["id"]))
        assert request.stage == "waiting_validation", request.error_code
    return db, settings, request, learning


@pytest.mark.parametrize(
    "fixture_ids",
    [
        ("data_identifiers", "data_free_text"),
        ("document_notes", "document_story"),
        ("coding_arithmetic", "coding_correct"),
    ],
)
async def test_actual_file_tools_and_bound_resume_preserve_separate_arms(
    trial_candidate, monkeypatch, fixture_ids
):
    db, settings, request, learning = await prepare(trial_candidate, fixture_ids)
    store = LocalArtifactStore(settings.artifact_root)
    async with db.session_factory() as session:
        bindings = list(await session.scalars(select(ValidationReplicaBindingRecord)))
    assert len(bindings) == 4
    registry = {item.fixture_id: item for item in default_personal_fixtures()}
    providers = {}
    roots = {}
    for binding in bindings:
        replica = await replica_factory(store).resume(
            binding.request_id,
            binding.case_key,
            binding.arm,
            binding.repeat_index,
            expected_manifest=binding.manifest,
        )
        roots[binding.run_id] = replica.root
        path, original = registry[binding.fixture_id].files[0]
        replacement = original.decode() + f"Verified by {binding.arm}.\n"
        providers[binding.run_id] = MockProvider(
            [
                ModelResponse(
                    message=Message(
                        role=MessageRole.ASSISTANT,
                        tool_calls=(
                            ToolCall(
                                call_id="read_input", name="file_read", arguments={"path": path}
                            ),
                        ),
                    ),
                    finish_reason=FinishReason.TOOL_CALLS,
                ),
                ModelResponse(
                    message=Message(
                        role=MessageRole.ASSISTANT,
                        tool_calls=(
                            ToolCall(
                                call_id="edit_input",
                                name="edit_file",
                                arguments={
                                    "path": path,
                                    "old_text": original.decode(),
                                    "replacement": replacement,
                                },
                            ),
                        ),
                    ),
                    finish_reason=FinishReason.TOOL_CALLS,
                ),
                ModelResponse(
                    message=Message(role=MessageRole.ASSISTANT, content="Verification done."),
                    finish_reason=FinishReason.STOP,
                ),
            ]
        )
    assert len(set(roots.values())) == 4
    monkeypatch.setattr(ConfiguredTaskHandler, "_provider", lambda _self, run_id: providers[run_id])
    tasks = JobWorker(
        worker_id="replica-file-test",
        lease_manager=JobLeaseManager(db.session_factory, lease_seconds=60),
        handler=ConfiguredTaskHandler(settings, db),
        heartbeat_seconds=10,
        poll_seconds=0.01,
    )
    coordinator = EvalCoordinator(db.session_factory, default_validator_registry())
    lease = await coordinator.claim_next("replica-eval")
    assert lease is not None
    for _ in range(4):
        assert await tasks.run_once()
        complete = await coordinator.run_once(lease)
    assert complete
    assert await learning.run_once()
    assert await learning.run_once()
    for binding in bindings:
        path, original = registry[binding.fixture_id].files[0]
        assert (
            roots[binding.run_id] / path
        ).read_bytes() == original + f"Verified by {binding.arm}.\n".encode()
        await replica_factory(store).resume(
            binding.request_id,
            binding.case_key,
            binding.arm,
            binding.repeat_index,
            expected_manifest=binding.manifest,
        )
        # The model actually read the initial bytes through the real file tool.
        observed = "\n".join(
            item.content or ""
            for item in providers[binding.run_id].requests[1].messages
            if item.role is MessageRole.TOOL
        )
        assert hashlib.sha256(original).hexdigest() in observed
        assert all(line in observed for line in original.decode().splitlines())
        assert registry[binding.fixture_id].files[0][1] == original
        async with db.session_factory() as session:
            output = await session.scalar(
                select(ArtifactRecord).where(
                    ArtifactRecord.run_id == binding.run_id,
                    ArtifactRecord.type == "validation_output",
                )
            )
        assert output is not None
        downloaded = await trial_candidate[0].get(f"/api/v1/artifacts/{output.id}/download")
        assert downloaded.status_code == 200
        assert downloaded.content == await store.read(output.uri)
        with zipfile.ZipFile(io.BytesIO(downloaded.content)) as archive:
            assert (
                archive.read(f"files/{path}") == original + f"Verified by {binding.arm}.\n".encode()
            )
            manifest = json.loads(archive.read("manifest.json"))
            assert manifest["run_id"] == str(binding.run_id)
            assert manifest["input_manifest_hash"] == binding.manifest_hash
            assert str(roots[binding.run_id]) not in archive.read("manifest.json").decode()
        # Download is an immutable byte snapshot, not a pointer to editable files.
        (roots[binding.run_id] / path).write_bytes(b"later local edit")
        repeated = await trial_candidate[0].get(f"/api/v1/artifacts/{output.id}/download")
        assert repeated.content == downloaded.content
    async with db.session_factory() as session:
        updated = await session.get(LearningRequestRecord, request.id)
        assert updated.stage == "validation_review", updated.error_code
        assert updated.validation_report["business_verification"] == "pending"
        assert updated.validation_report["trial_eligible"] is False


async def test_replica_manifest_change_stops_next_boundary_without_rewriting(trial_candidate):
    db, settings, request, _ = await prepare(trial_candidate)
    async with db.session_factory() as session:
        binding = await session.scalar(select(ValidationReplicaBindingRecord))
    store = LocalArtifactStore(settings.artifact_root)
    replica = await replica_factory(store).resume(
        binding.request_id,
        binding.case_key,
        binding.arm,
        binding.repeat_index,
        expected_manifest=binding.manifest,
    )
    guard = await PersonalValidationRunGuard.for_run(
        db.session_factory,
        store,
        binding.run_id,
        learning_enabled=True,
    )
    await guard.check()
    (replica.root / ".validation-input-manifest.json").write_text(json.dumps({"forged": True}))
    with pytest.raises(MemoryError, match="replica_unavailable"):
        await guard.check()
    assert json.loads((replica.root / ".validation-input-manifest.json").read_text()) == {
        "forged": True
    }


async def test_binding_identity_is_immutable(trial_candidate):
    db, _, _, _ = await prepare(trial_candidate)
    async with db.session_factory() as session:
        binding = await session.scalar(select(ValidationReplicaBindingRecord))
        binding.arm = "treatment" if binding.arm == "control" else "control"
        with pytest.raises(ValueError, match="immutable"):
            await session.flush()
        await session.rollback()


async def test_cancelled_request_cannot_reopen_its_replica_scope(trial_candidate):
    from evoagent.db.models import RunRecord, TaskRecord
    from evoagent.learning.service import LearningService

    db, settings, request, _ = await prepare(trial_candidate)
    async with db.session_factory() as session:
        binding = await session.scalar(select(ValidationReplicaBindingRecord))
        run = await session.get(RunRecord, binding.run_id)
        task = await session.get(TaskRecord, run.task_id)
        # The private execution root does not grant a user-project scope.
        assert task.project_id is None and task.project_authorization_version is None
    guard = await PersonalValidationRunGuard.for_run(
        db.session_factory,
        LocalArtifactStore(settings.artifact_root),
        binding.run_id,
        learning_enabled=True,
    )
    project = await guard.execution_project()
    assert project.id == binding.id and project.writable
    await LearningService(db.session_factory).cancel_request(request.id, request.lock_version)
    with pytest.raises(MemoryError, match="authorization_revoked"):
        await guard.execution_project()


async def test_initial_replica_input_change_is_refused_before_run_configuration(trial_candidate):
    db, settings, _, _ = await prepare(trial_candidate)
    async with db.session_factory() as session:
        binding = await session.scalar(select(ValidationReplicaBindingRecord))
    store = LocalArtifactStore(settings.artifact_root)
    replica = await replica_factory(store).resume(
        binding.request_id,
        binding.case_key,
        binding.arm,
        binding.repeat_index,
        expected_manifest=binding.manifest,
    )
    path = replica.root / binding.manifest["files"][0]["path"]
    path.write_bytes(b"changed before execution")
    guard = await PersonalValidationRunGuard.for_run(
        db.session_factory,
        store,
        binding.run_id,
        learning_enabled=True,
    )
    with pytest.raises(MemoryError, match="replica_unavailable"):
        await guard.check()
    assert path.read_bytes() == b"changed before execution"


async def test_output_archive_retry_is_idempotent_and_cancel_stops_new_snapshot(trial_candidate):
    from evoagent.learning.replica_outputs import archive_outputs
    from evoagent.learning.service import LearningService
    from evoagent.privacy.artifact_access import ArtifactInjectionGuard, ArtifactNotInjectable
    from evoagent.tasks.lease_guard import LeaseGuard, LeaseLostError

    db, settings, request, _ = await prepare(trial_candidate)
    lease = await JobLeaseManager(db.session_factory, lease_seconds=60).claim_next("archive-retry")
    assert lease is not None
    store = LocalArtifactStore(settings.artifact_root)
    guard = await PersonalValidationRunGuard.for_run(
        db.session_factory, store, lease.run_id, learning_enabled=True
    )
    service = ArtifactService(store, db.session_factory, lease_guard=LeaseGuard(lease))
    first = await archive_outputs(guard, service)
    second = await archive_outputs(guard, service)
    assert first.id == second.id and first.content_hash == second.content_hash
    async with db.session_factory() as session:
        outputs = list(
            await session.scalars(
                select(ArtifactRecord).where(
                    ArtifactRecord.run_id == lease.run_id,
                    ArtifactRecord.type == "validation_output",
                )
            )
        )
    assert len(outputs) == 1
    injection = ArtifactInjectionGuard(session_factory=db.session_factory, artifact_store=store)
    with pytest.raises(ArtifactNotInjectable):
        await injection.read_verified_text(
            artifact_id=second.id, run_id=lease.run_id, purpose="artifact_read"
        )
    from evoagent.db.models import TaskRecord

    async with db.session_factory() as session:
        task = await session.get(TaskRecord, lease.task_id)
        task.lease_epoch += 1
        await session.commit()
    with pytest.raises(LeaseLostError):
        await archive_outputs(guard, service)
    await LearningService(db.session_factory).cancel_request(request.id, request.lock_version)
    with pytest.raises(MemoryError, match="authorization_revoked"):
        await archive_outputs(guard, service)
    assert await store.read(second.uri)
