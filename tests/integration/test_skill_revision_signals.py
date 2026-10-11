"""Repeated attributed failures suggest a revision, never authorize paid learning."""

from copy import deepcopy
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from evoagent.db.models import LearningRequestRecord, RunRecord, RunSkillSelectionRecord, TaskRecord
from evoagent.learning.repository import LearningRepository
from evoagent.learning.schema import FeedbackPayload
from evoagent.skills.trials import TrialScope
from evoagent.skills.usage import SkillUsageService
from tests.integration.test_skill_usage_collection import setup_use

pytest_plugins = ("tests.integration.test_personal_trials",)


async def failures(
    context,
    *,
    attribution="skill_related",
    same_input=False,
    consent=False,
    independent_files=False,
):
    db, first, version, artifact = await setup_use(context)
    uses = [first]
    async with db.session_factory() as session:
        task = await session.get(TaskRecord, first.task_id)
        binding = await session.scalar(
            select(RunSkillSelectionRecord).where(RunSkillSelectionRecord.run_id == first.id)
        )
        for index in range(2):
            next_task = TaskRecord(
                session_id=task.session_id,
                goal=task.goal if same_input else f"independent {index}",
                family=task.family,
                status="completed",
                frozen_inputs=(
                    {"files": [{**task.frozen_inputs["files"][0], "sha256": str(index + 1) * 64}]}
                    if independent_files
                    else deepcopy(task.frozen_inputs)
                ),
            )
            session.add(next_task)
            await session.flush()
            run = RunRecord(
                task_id=next_task.id,
                provider="mock",
                model="mock",
                status="completed",
                data_role="personal",
                ended_at=datetime.now(UTC),
                config_snapshot=deepcopy(first.config_snapshot),
                config_hash=first.config_hash,
            )
            session.add(run)
            await session.flush()
            session.add(
                RunSkillSelectionRecord(
                    run_id=run.id,
                    skill_version_id=version.id,
                    trial_id=binding.trial_id,
                    origin="trial",
                    mode="retrieval",
                    rank=1,
                    score=1,
                    query_terms=[],
                    content_hash=binding.content_hash,
                    rendered_hash=binding.rendered_hash,
                    scope_key=binding.scope_key,
                    applicability=deepcopy(binding.applicability),
                    selection_policy_version=binding.selection_policy_version,
                )
            )
            # Each claim must refer to its own run artifact.
            from evoagent.db.models import ArtifactRecord

            copied = ArtifactRecord(
                run_id=run.id,
                type=artifact.type,
                uri=artifact.uri,
                content_hash=artifact.content_hash,
                size_bytes=artifact.size_bytes,
                redaction_status=artifact.redaction_status,
                redaction_policy_version=artifact.redaction_policy_version,
                redaction_checked_hash=artifact.redaction_checked_hash,
            )
            session.add(copied)
            await session.flush()
            uses.append((run, copied))
        await session.commit()
    observations = []
    for entry in [(first, artifact), *uses[1:]]:
        run, proof = entry
        claim = {
            "type": "skill_observation",
            "schema_version": 1,
            "version_id": str(version.id),
            "criterion_id": "identifiers_preserved",
            "outcome": "verified_failure",
            "attribution": attribution,
            "associated_steps": [version.definition["steps"][0]["id"]],
            "artifacts": [{"artifact_id": str(proof.id), "content_hash": proof.content_hash}],
        }
        async with db.session_factory() as session:
            feedback = await LearningRepository(session).append_feedback(
                run.id,
                "failure-proof",
                FeedbackPayload(
                    intent="method",
                    verdict="incorrect",
                    evidence_refs=[claim],
                    learn_from_feedback=consent,
                ),
                "local-user",
            )
            await session.commit()
        observations.extend(
            await SkillUsageService(db.session_factory).collect(run.id, feedback.revision)
        )
    return db, version, observations


async def test_independent_same_problem_suggests_without_enqueuing(trial_candidate):
    client, _, _, state, _, skill = trial_candidate
    db, version, observations = await failures(trial_candidate)
    usage = SkillUsageService(db.session_factory)
    result = await usage.suggest_revision(version.id, TrialScope(skill.workspace_id))
    assert result["independent_input_count"] == 3
    assert set(result["observation_ids"]) == set(observations)
    assert result["requires_explicit_submission"] and not result["automatically_queued"]
    async with db.session_factory() as session:
        assert len(list(await session.scalars(select(LearningRequestRecord)))) == 1
    response = await client.get(
        f"/api/v1/skill-versions/{version.id}/revision-signal",
        params={"workspace_id": str(skill.workspace_id)},
    )
    assert response.status_code == 200 and response.json()["independent_input_count"] == 3
    revoked = await client.post(
        f"/api/v1/learning-sources/{state['source']['id']}/revoke",
        json={"reason": "withdraw", "expected_status": "valid"},
    )
    assert revoked.status_code == 200
    assert await usage.suggest_revision(version.id, TrialScope(skill.workspace_id)) is None


@pytest.mark.parametrize(
    "attribution,same_input",
    [("environment", False), ("uncertain", False), ("skill_related", True)],
)
async def test_environment_and_replayed_input_are_not_revision_evidence(
    trial_candidate, attribution, same_input
):
    db, version, _ = await failures(trial_candidate, attribution=attribution, same_input=same_input)
    scope = TrialScope(trial_candidate[-1].workspace_id)
    assert await SkillUsageService(db.session_factory).suggest_revision(version.id, scope) is None
