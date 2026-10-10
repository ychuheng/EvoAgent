"""Actual dispatch -> synthetic judgment -> ordinary use -> durable safety brake.

All providers are injected offline; user business judgments are fixtures. This
proves wiring, not model efficacy, factual accuracy or approved real spending.
"""

from sqlalchemy import select

from evoagent.core.models import (
    FinishReason,
    Message,
    MessageRole,
    ModelResponse,
    TokenUsage,
    ToolCall,
)
from evoagent.db.models import (
    RunRecord,
    SkillObservationRecord,
    SkillTrialRecord,
    SkillVersionRecord,
)
from evoagent.memory.maintenance import MaintenanceWorker
from evoagent.projects.service import ProjectService
from evoagent.providers.mock import MockProvider
from evoagent.skills.observation_jobs import ObservationJobHandler
from evoagent.tasks.lease import JobLeaseManager
from evoagent.tasks.service import TaskService
from evoagent.workers.bootstrap import ConfiguredTaskHandler
from tests.integration import test_real_validation_contract as real_contract

pytest_plugins = ("tests.integration.test_personal_selection_v3",)


async def test_actual_personal_use_feedback_and_three_failures_stop_next_adoption(
    selection_candidate, monkeypatch, tmp_path
):
    await (
        real_contract.test_real_validation_uses_task_bound_learning_ledger_without_double_charging(
            selection_candidate, monkeypatch, tmp_path
        )
    )
    client, db, *_ = selection_candidate
    settings = client._transport.app.state.settings
    safety = MaintenanceWorker(
        db.session_factory,
        None,
        lane="critical",
        handlers={"learning_observe": ObservationJobHandler(db.session_factory)},
        allowed_kinds={"learning_observe"},
    )
    while await safety.run_once():
        pass
    async with db.session_factory() as session:
        trial = await session.scalar(select(SkillTrialRecord))
        assert trial.status == "active"
        from evoagent.skills.usage import SkillUsageService

        health = await SkillUsageService(db.session_factory).health_snapshot(session, trial)
        assert health.status == "healthy", health
        version = await session.get(SkillVersionRecord, trial.version_id)
        original_definition = version.definition
    root = tmp_path / "brake-project"
    root.mkdir()
    project = await ProjectService(db.session_factory).register(path=str(root))
    tasks = TaskService(db.session_factory)
    chat = await tasks.create_session("independent failure fixtures")
    model_requests = []

    def provider(_self, run_id):
        mock = MockProvider(
            [
                ModelResponse(
                    message=Message(
                        role=MessageRole.ASSISTANT,
                        tool_calls=(
                            ToolCall(
                                call_id="write-proof",
                                name="file_write",
                                arguments={"path": "result.csv", "content": "identifier\n50\n"},
                            ),
                        ),
                    ),
                    finish_reason=FinishReason.TOOL_CALLS,
                    usage=TokenUsage(input_tokens=2, output_tokens=1, total_tokens=3),
                ),
                ModelResponse(
                    message=Message(role=MessageRole.ASSISTANT, content="synthetic output"),
                    finish_reason=FinishReason.STOP,
                    usage=TokenUsage(input_tokens=2, output_tokens=1, total_tokens=3),
                ),
            ]
        )
        model_requests.append(mock)
        return mock

    monkeypatch.setattr(ConfiguredTaskHandler, "_provider", provider)
    runs = []
    for index in range(4):
        (root / "input.csv").write_text(f"identifier\n000{index + 50}\n", encoding="utf-8")
        task = await tasks.create_task(
            session_id=chat.id,
            goal="核验 验证 检查",
            family="data",
            provider=settings.provider.value,
            model=settings.model,
            project_id=project.id,
            input_paths=["input.csv"],
        )
        lease = await JobLeaseManager(db.session_factory, lease_seconds=120).claim_next("brake-e2e")
        assert lease.run_id == task.run.id
        result = await ConfiguredTaskHandler(settings, db).handle(lease)
        assert str(result.status) == "completed", result.error_code
        await JobLeaseManager(db.session_factory, lease_seconds=120).finalize(lease, result)
        async with db.session_factory() as session:
            run = await session.get(RunRecord, lease.run_id)
            chosen = run.config_snapshot["selected_skills"]
            if index == 3:
                assert chosen == []
                assert (await session.get(SkillTrialRecord, trial.id)).status == "suspended"
                break
            assert chosen, f"missing trial selection at run {index}"
            assert chosen[0]["trial_id"] == str(trial.id)
        catalog = await client.get(f"/api/v1/runs/{lease.run_id}/skill-observation-evidence")
        assert catalog.status_code == 200, catalog.text
        evidence = catalog.json()
        assert evidence["pending_artifacts"], "actual write must supply durable proof artifacts"
        checked = await client.post(
            f"/api/v1/runs/{lease.run_id}/skill-observation-evidence/verify",
            json={
                "artifacts": [
                    {"artifact_id": item["artifact_id"], "content_hash": item["content_hash"]}
                    for item in evidence["pending_artifacts"][:1]
                ]
            },
        )
        assert checked.status_code == 200, checked.text
        evidence = checked.json()
        assert evidence["artifacts"], "verified actual output is now eligible for a human claim"
        selected = evidence["versions"][0]
        feedback = await client.post(
            f"/api/v1/runs/{lease.run_id}/feedback",
            json={
                "client_request_id": f"brake-claim-{index}",
                "intent": "method",
                "verdict": "incorrect",
                "learn_from_feedback": False,
                "evidence_refs": [
                    {
                        "type": "skill_observation",
                        "schema_version": 1,
                        "version_id": selected["version_id"],
                        "criterion_id": "identifiers_preserved",
                        "outcome": "verified_failure",
                        "attribution": "skill_related",
                        "associated_steps": [selected["steps"][0]],
                        "artifacts": [
                            {
                                "artifact_id": item["artifact_id"],
                                "content_hash": item["content_hash"],
                            }
                            for item in evidence["artifacts"][:1]
                        ],
                    }
                ],
            },
        )
        assert feedback.status_code == 201, feedback.text
        while await safety.run_once():
            pass
        runs.append(lease.run_id)
    async with db.session_factory() as session:
        observed = list(
            await session.scalars(
                select(SkillObservationRecord).where(
                    SkillObservationRecord.run_id.in_(runs),
                    SkillObservationRecord.feedback_revision > 0,
                )
            )
        )
        assert len(observed) == 3
        assert all(
            item.outcome == "verified_failure" and item.attribution == "skill_related"
            for item in observed
        )
        assert len({item.input_fingerprint for item in observed}) == 3
        saved = await session.get(SkillTrialRecord, trial.id)
        assert saved.status == "suspended" and saved.suspension_reason == "verified_skill_failures"
        assert (
            await session.get(SkillVersionRecord, trial.version_id)
        ).definition == original_definition
    assert len(model_requests) == 4 and all(len(item.requests) == 2 for item in model_requests)
