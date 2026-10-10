"""Original project grants Skill scope only; execution uses registered private copies."""

from uuid import UUID

from sqlalchemy import select

from evoagent.core.models import FinishReason, Message, MessageRole, ModelResponse, ToolCall
from evoagent.db.models import (
    EvalRunRecord,
    LearningRequestRecord,
    RunRecord,
    SkillRecord,
    SkillVersionRecord,
    TaskRecord,
    ValidationReplicaBindingRecord,
)
from evoagent.learning.replica_bindings import replica_factory
from evoagent.learning.replicas import default_personal_fixtures
from evoagent.memory.maintenance import MaintenanceWorker
from evoagent.projects.service import ProjectService
from evoagent.providers.mock import MockProvider
from evoagent.trace.artifacts import LocalArtifactStore
from evoagent.workers.bootstrap import ConfiguredTaskHandler
from tests.integration.test_learning_worker import submit, worker
from tests.integration.test_personal_selection_v3 import DataGenerator, execute
from tests.integration.test_validation_admission import inputs

pytest_plugins = ("tests.integration.test_learning_api",)


async def project_candidate(context, tmp_path):
    client, db, run_id, settings = context
    root = tmp_path / "user-project"
    root.mkdir()
    (root / "original.csv").write_text("identifier\n00091\n", encoding="utf8")
    project = await ProjectService(db.session_factory).register(path=str(root))
    async with db.session_factory() as session:
        source_run = await session.get(RunRecord, run_id)
        task = await session.get(TaskRecord, source_run.task_id)
        task.project_id = project.id
        task.project_authorization_version = project.authorization_version
        await session.commit()
    requested = await submit(client, run_id)
    runtime = worker(db, settings, DataGenerator())
    for _ in range(3):
        assert await runtime.run_once()
    state = (await client.get(f"/api/v1/learning-requests/{requested['id']}")).json()
    async with db.session_factory() as session:
        version = await session.get(SkillVersionRecord, UUID(state["candidate_version_id"]))
        skill = await session.get(SkillRecord, version.skill_id)
        assert skill.project_id == project.id
    return (client, db, run_id, state, version, skill), project, root


async def test_project_scoped_candidate_runs_only_in_public_private_replicas(
    learning_api, tmp_path, monkeypatch
):
    context, project, root = await project_candidate(learning_api, tmp_path)
    client, db, *_ = context
    settings = learning_api[3]
    _, parent, payload, _ = await inputs(context)
    body = payload.model_dump(mode="json")
    for case, fixture in zip(body["cases"], ("data_identifiers", "data_free_text"), strict=True):
        case["fixture_id"] = fixture
        case["public_input"] = {"goal": "Check the independent registered input"}
    response = await client.post(f"/api/v1/learning-requests/{parent.id}/validations", json=body)
    assert response.status_code == 202, response.text
    prepared = response.json()
    assert prepared["policy_snapshot"]["source_project_id"] == str(project.id)
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
        bindings = list(await session.scalars(select(ValidationReplicaBindingRecord)))
    registry = {item.fixture_id: item for item in default_personal_fixtures()}
    providers, roots = {}, {}
    for binding in bindings:
        replica = await replica_factory(LocalArtifactStore(settings.artifact_root)).resume(
            binding.request_id,
            binding.case_key,
            binding.arm,
            binding.repeat_index,
            expected_manifest=binding.manifest,
        )
        roots[binding.run_id] = replica.root
        path, original = registry[binding.fixture_id].files[0]
        calls = [
            ToolCall(
                call_id="reject-original",
                name="file_read",
                arguments={"path": str(root / "original.csv")},
            ),
            ToolCall(call_id="read-private", name="file_read", arguments={"path": path}),
            ToolCall(
                call_id="edit-private",
                name="edit_file",
                arguments={
                    "path": path,
                    "old_text": original.decode(),
                    "replacement": original.decode() + "Private edit only.\n",
                },
            ),
        ]
        providers[binding.run_id] = MockProvider(
            [
                *(
                    ModelResponse(
                        message=Message(role=MessageRole.ASSISTANT, tool_calls=(call,)),
                        finish_reason=FinishReason.TOOL_CALLS,
                    )
                    for call in calls
                ),
                ModelResponse(
                    message=Message(
                        role=MessageRole.ASSISTANT, content="offline fixture completed"
                    ),
                    finish_reason=FinishReason.STOP,
                ),
            ]
        )
    monkeypatch.setattr(ConfiguredTaskHandler, "_provider", lambda _self, run_id: providers[run_id])
    await execute(db, settings)
    assert await learning.run_once()
    assert await learning.run_once()
    async with db.session_factory() as session:
        request = await session.get(LearningRequestRecord, UUID(prepared["id"]))
        assert request.stage == "validation_review", request.error_code
        assert request.project_id == project.id
        assert not request.validation_report["trial_eligible"]
        for evaluation in await session.scalars(select(EvalRunRecord)):
            task = await session.get(TaskRecord, evaluation.task_id)
            assert task.project_id is None
            run = await session.get(RunRecord, evaluation.run_id)
            assert run.status == "completed", (run.error_code, run.error_message)
            for selection in run.config_snapshot["selected_skills"]:
                assert selection["scope"]["project_id"] == str(project.id)
        assert request.validation_report["adoption_verification"] == "passed", [
            (item["case_kind"], item["arm"], item.get("actual_selection"))
            for item in request.validation_report["items"]
        ]
    assert (root / "original.csv").read_text(encoding="utf8") == "identifier\n00091\n"
    assert list(root.iterdir()) == [root / "original.csv"]
    assert len(set(roots.values())) == 4
    for binding in bindings:
        path, _ = registry[binding.fixture_id].files[0]
        assert (
            (roots[binding.run_id] / path)
            .read_text(encoding="utf8")
            .endswith("Private edit only.\n")
        )
        tool_messages = [
            message
            for call in providers[binding.run_id].requests
            for message in call.messages
            if message.role == MessageRole.TOOL
        ]
        assert not any("00091" in (message.content or "") for message in tool_messages)


async def test_project_validation_rejects_cases_without_private_files(learning_api, tmp_path):
    context, _, _ = await project_candidate(learning_api, tmp_path)
    _, parent, payload, _ = await inputs(context)
    response = await context[0].post(
        f"/api/v1/learning-requests/{parent.id}/validations", json=payload.model_dump(mode="json")
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "validation_project_replica_required"


async def test_original_project_revocation_blocks_validation_before_dispatch(
    learning_api, tmp_path
):
    context, project, _ = await project_candidate(learning_api, tmp_path)
    client, db, *_ = context
    _, parent, payload, _ = await inputs(context)
    body = payload.model_dump(mode="json")
    for case, fixture in zip(body["cases"], ("data_identifiers", "data_free_text"), strict=True):
        case["fixture_id"] = fixture
        case["public_input"] = {"goal": "Check registered input"}
    response = await client.post(f"/api/v1/learning-requests/{parent.id}/validations", json=body)
    assert response.status_code == 202, response.text
    prepared = response.json()
    await ProjectService(db.session_factory).revoke(project.id, reason="fixture scope revoked")
    response = await client.post(
        f"/api/v1/learning-requests/{prepared['id']}/validation-start",
        json={"expected_lock_version": prepared["lock_version"]},
    )
    assert response.status_code == 422
    async with db.session_factory() as session:
        assert not list(await session.scalars(select(EvalRunRecord)))
