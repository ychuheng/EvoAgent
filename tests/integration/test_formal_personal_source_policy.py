"""Synthetic source fixtures verify admission contracts, never model efficacy."""

from uuid import UUID

import pytest
from sqlalchemy import select

from evoagent.db.models import (
    EvalDatasetRecord,
    EvalExperimentRecord,
    LearningSourceRecord,
    RunEventRecord,
    RunRecord,
    SkillSourceRecord,
    SkillVersionRecord,
)
from evoagent.evals.gates import GateReport, QualityGate, requires_personal_source_policy
from evoagent.evals.metrics import EvaluationReport
from evoagent.evals.validators import default_validator_registry
from evoagent.skills.canonical import content_hash
from evoagent.skills.provenance import FormalSourcePolicy
from evoagent.skills.service import GateNotPassedError, SkillService
from evoagent.skills.validation import SkillDefinitionValidator
from evoagent.tools.catalog import default_skill_tool_catalog
from evoagent.trace.artifacts import ArtifactService, LocalArtifactStore
from tests.integration.test_learning_worker import submit, worker

pytest_plugins = ("tests.integration.test_learning_api",)


@pytest.mark.parametrize("accepted", [False, True, "conflicting"])
async def test_personal_formal_policy_requires_positive_evidence_and_mock_cannot_publish(
    learning_api, accepted
):
    client, db, run_id, settings = learning_api
    expected_success = accepted is True
    if accepted:
        async with db.session_factory() as session:
            (await session.get(RunRecord, run_id)).next_event_sequence = (
                3 if accepted == "conflicting" else 2
            )
            session.add(
                RunEventRecord(
                    run_id=run_id,
                    sequence=1,
                    event_type="acceptance.checked",
                    payload={"passed": True},
                )
            )
            if accepted == "conflicting":
                session.add(
                    RunEventRecord(
                        run_id=run_id,
                        sequence=2,
                        event_type="acceptance.checked",
                        payload={"passed": False},
                    )
                )
            await session.commit()
    request = await submit(client, run_id)
    runner = worker(db, settings)
    for _ in range(3):
        assert await runner.run_once()
    state = (await client.get(f"/api/v1/learning-requests/{request['id']}")).json()
    version_id = UUID(state["candidate_version_id"])
    artifacts = ArtifactService(LocalArtifactStore(settings.artifact_root), db.session_factory)
    async with db.session_factory() as session:
        source = await session.scalar(select(SkillSourceRecord))
        version = await session.get(SkillVersionRecord, version_id)
        dataset = EvalDatasetRecord(name="formal_fixture", version=1, content_hash=content_hash({}))
        session.add(dataset)
        await session.flush()
        config = {"provider": "mock", "model": "mock", "repeats": 1, "code_version": "offline"}
        experiment = EvalExperimentRecord(
            kind="skill_comparison",
            dataset_id=dataset.id,
            skill_version_id=version.id,
            config_snapshot=config,
            config_hash=content_hash(config),
            purpose="formal",
        )
        session.add(experiment)
        await session.commit()
    check = await FormalSourcePolicy(db.session_factory, artifacts).validate(source)
    assert check.eligible and check.counts_as_success is expected_success
    assert (check.input_fingerprint is not None) is expected_success
    # No evaluation is dispatched. A zero-pair report must stay non-publishable,
    # and the explicit Mock gate must still reject even positive source fixtures.
    report = EvaluationReport(
        experiment_id=experiment.id,
        dataset_id=dataset.id,
        skill_version_id=version.id,
        config_hash=experiment.config_hash,
        pair_count=0,
        comparable_pairs=0,
        baseline_successes=0,
        skill_successes=0,
        baseline_success_rate=0,
        skill_success_rate=0,
        safety_regressions=0,
        efficiency_comparable_pairs=0,
        families=(),
        pairs=(),
    )
    gate = QualityGate(
        db.session_factory,
        SkillDefinitionValidator(
            default_skill_tool_catalog(),
            supported_schema_version=2,
            allowed_tools=frozenset({"file_read"}),
        ),
        default_validator_registry(),
        artifacts,
        minimum_sources=0,
    )
    result = await gate.evaluate(report)
    assert result.schema_version == 2 and result.source_policy_version == "formal-source:v1"
    checks = {c.name: c for c in result.checks}
    assert checks["minimum_independent_sources"].threshold == 1
    assert checks["minimum_independent_sources"].passed is expected_success
    assert not checks["personal_formal_requires_real_execution"].passed
    assert not result.passed
    if expected_success:
        # Synthetic reports test publication validation only. They are not
        # evidence that this Mock experiment passed the actual QualityGate.
        legacy = GateReport(
            skill_version_id=version.id,
            experiment_id=experiment.id,
            passed=True,
            checks=(),
        )
        incomplete = result.model_copy(update={"passed": True})
        complete = result.model_copy(
            update={
                "passed": True,
                "checks": tuple(c.model_copy(update={"passed": True}) for c in result.checks),
            }
        )
        for synthetic, error in (
            ({**result.model_dump(mode="json"), "source_policy_version": "unknown"}, "malformed"),
            (legacy, "requires a formal v2"),
            (incomplete, "hard failure"),
            (complete, None),
        ):
            async with db.session_factory() as session:
                current_version = await session.get(SkillVersionRecord, version.id)
                current_experiment = await session.get(EvalExperimentRecord, experiment.id)
                body = (
                    synthetic if isinstance(synthetic, dict) else synthetic.model_dump(mode="json")
                )
                current_version.gate_report_hash = content_hash(body)
                current_experiment.gate_report = body
                current_experiment.gate_report_hash = content_hash(body)
                await session.flush()
                if error:
                    with pytest.raises(GateNotPassedError, match=error):
                        await SkillService.check_formal_gate(session, current_version)
                    # Each rejected fixture is rolled back. Never rewrite a
                    # committed immutable report to run the next scenario.
                else:
                    await SkillService.check_formal_gate(session, current_version)
                    await session.commit()
        async with db.session_factory() as session:
            child = SkillVersionRecord(
                skill_id=version.skill_id,
                parent_version_id=version.id,
                version=version.version + 1,
                schema_version=version.schema_version,
                definition=version.definition,
                content_hash=version.content_hash,
                extraction_key=content_hash(
                    {"test": "ancestor-only-personal", "id": str(version.id)}
                ),
            )
            session.add(child)
            await session.flush()
            # Even an incorrectly created child lacking direct source links
            # cannot be treated as a legacy TRAIN-only candidate.
            assert await requires_personal_source_policy(session, child)
            await session.commit()
    async with db.session_factory() as session:
        frozen = await session.get(LearningSourceRecord, source.learning_source_id)
        frozen.status = "revoked"
        frozen.revocation_epoch += 1
        await session.commit()
    if expected_success:
        async with db.session_factory() as session:
            current_version = await session.get(SkillVersionRecord, version.id)
            with pytest.raises(GateNotPassedError, match="source"):
                await SkillService.check_formal_gate(session, current_version)
    revoked = await FormalSourcePolicy(db.session_factory, artifacts).validate(source)
    assert not revoked.eligible and not revoked.counts_as_success
    changed = await gate.evaluate(report)
    assert not {c.name: c for c in changed.checks}["sources_eligible_under_policy"].passed
