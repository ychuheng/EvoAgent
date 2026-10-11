"""分层 QualityGate 与 SkillVersion 评测生命周期推进。"""

import asyncio
import hashlib
from dataclasses import asdict
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_serializer, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from evoagent.db.models import (
    ArtifactRecord,
    EvalCaseRecord,
    EvalRunRecord,
    SkillRecord,
    SkillSourceRecord,
    SkillVersionRecord,
)
from evoagent.db.unit_of_work import UnitOfWork
from evoagent.evals.coordinator import EvalCoordinator
from evoagent.evals.lifecycle import EvalExperimentStatus, EvalSplit
from evoagent.evals.metrics import EvaluationReport, EvaluationReportService
from evoagent.evals.validators.base import ValidatorRegistry
from evoagent.skills.access import SkillAccessPolicy, source_graph_identity
from evoagent.skills.canonical import content_hash
from evoagent.skills.lifecycle import (
    SkillVersionStatus,
    ensure_skill_version_transition,
)
from evoagent.skills.provenance import FormalSourcePolicy
from evoagent.skills.schema import SkillDefinition
from evoagent.skills.validation import SkillDefinitionValidator
from evoagent.trace.artifacts import ArtifactService

PERSONAL_FORMAL_HARD_CHECKS = frozenset(
    {
        "skill_definition_valid",
        "skill_content_hash_matches",
        "experiment_config_hash_matches",
        "minimum_independent_sources",
        "sources_eligible_under_policy",
        "personal_formal_requires_real_execution",
        "personal_formal_has_successful_treatment",
        "source_trace_hashes_match",
        "validator_versions_supported",
        "all_pairs_comparable",
        "overall_success_not_regressed",
        "task_families_not_regressed",
        "no_safety_regression",
    }
)


class GateCheck(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    layer: str
    passed: bool
    threshold: Any | None = None
    actual: Any | None = None
    evidence: dict[str, Any] = Field(default_factory=dict)


class GateReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: int = 1
    skill_version_id: UUID
    experiment_id: UUID
    passed: bool
    checks: tuple[GateCheck, ...]
    source_policy_version: str | None = None

    @model_validator(mode="after")
    def validate_source_policy(self):
        if self.schema_version == 1:
            if self.source_policy_version is not None:
                raise ValueError("legacy gate cannot carry a new source policy")
        elif self.schema_version == 2:
            if self.source_policy_version != "formal-source:v1":
                raise ValueError("unsupported formal source policy")
        else:
            raise ValueError("unsupported gate schema")
        return self

    @model_serializer(mode="wrap")
    def compatible_serialization(self, handler):
        value = handler(self)
        if self.schema_version == 1:
            value.pop("source_policy_version", None)
        return value

    def report_hash(self) -> str:
        return content_hash(self.model_dump(mode="json"))


async def requires_personal_source_policy(session, version) -> bool:
    """A revision cannot erase its parent's personal admission obligations."""
    seen = set()
    while version is not None:
        if version.id in seen or len(seen) >= 32:
            raise ValueError("formal source lineage is cyclic or exceeds its budget")
        seen.add(version.id)
        personal = await session.scalar(
            select(SkillSourceRecord.id)
            .where(
                SkillSourceRecord.skill_version_id == version.id,
                SkillSourceRecord.source_kind == "personal",
            )
            .limit(1)
        )
        if personal is not None or version.extraction_key.startswith("merge:"):
            return True  # All supported merge roots require the personal contract.
        if version.parent_version_id is None:
            return False
        version = await session.get(SkillVersionRecord, version.parent_version_id)
        if version is None:
            raise ValueError("formal source lineage parent is missing")
    return False


class QualityGate:
    """正确性和安全属于硬门禁，效率只能作为人工评审信息。"""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        definition_validator: SkillDefinitionValidator,
        validators: ValidatorRegistry,
        artifacts: ArtifactService,
        *,
        minimum_sources: int,
    ) -> None:
        self._session_factory = session_factory
        self._definition_validator = definition_validator
        self._validators = validators
        self._artifacts = artifacts
        self._minimum_sources = minimum_sources

    async def evaluate(self, report: EvaluationReport) -> GateReport:
        if report.purpose != "formal":
            raise ValueError("formal quality gate rejects personal validation reports")
        async with UnitOfWork(self._session_factory) as unit:
            version = await unit.skill_versions.get(report.skill_version_id)
            experiment = await unit.evals.get_experiment(report.experiment_id)
            if experiment.purpose != "formal":
                raise ValueError("formal quality gate rejects personal validation reports")
            definition = SkillDefinition.model_validate(version.definition)
            sources = tuple(
                await unit.session.scalars(
                    select(SkillSourceRecord).where(
                        SkillSourceRecord.skill_version_id == version.id
                    )
                )
            )
            eval_rows = tuple(
                await unit.session.execute(
                    select(EvalRunRecord, EvalCaseRecord)
                    .join(EvalCaseRecord, EvalCaseRecord.id == EvalRunRecord.eval_case_id)
                    .where(EvalRunRecord.id.in_([item.source_eval_run_id for item in sources]))
                )
            )
            source_artifacts = {
                item.id: item
                for item in await unit.session.scalars(
                    select(ArtifactRecord).where(
                        ArtifactRecord.id.in_([item.trace_artifact_id for item in sources])
                    )
                )
            }
            personal_policy = await requires_personal_source_policy(unit.session, version)

        source_checks, graph_identity, graph_valid = [], [], True
        if personal_policy:
            source_checks, graph_identity, graph_valid = await self._personal_sources(
                version, sources
            )

        checks: list[GateCheck] = []
        try:
            self._definition_validator.validate(definition)
            static_error = None
        except ValueError as error:
            static_error = str(error)
        checks.append(
            GateCheck(
                name="skill_definition_valid",
                layer="static",
                passed=static_error is None,
                actual=static_error or "valid",
            )
        )
        actual_hash = content_hash(definition.model_dump(mode="json"))
        checks.append(
            GateCheck(
                name="skill_content_hash_matches",
                layer="static",
                passed=actual_hash == version.content_hash,
                threshold=version.content_hash,
                actual=actual_hash,
            )
        )
        recomputed_config_hash = content_hash(experiment.config_snapshot)
        checks.append(
            GateCheck(
                name="experiment_config_hash_matches",
                layer="static",
                passed=(
                    experiment.skill_version_id == version.id
                    and experiment.dataset_id == report.dataset_id
                    and report.config_hash == experiment.config_hash == recomputed_config_hash
                ),
                threshold=experiment.config_hash,
                actual=report.config_hash,
                evidence={"recomputed_hash": recomputed_config_hash},
            )
        )
        required_sources = (
            max(1, self._minimum_sources) if personal_policy else self._minimum_sources
        )
        independent_sources = (
            len({check.input_fingerprint for check in source_checks if check.counts_as_success})
            if personal_policy
            else len({item.source_run_id for item in sources})
        )
        checks.append(
            GateCheck(
                name="minimum_independent_sources",
                layer="source",
                passed=independent_sources >= required_sources,
                threshold=required_sources,
                actual=independent_sources,
                evidence={"source_ids": [str(item.id) for item in sources]},
            )
        )
        source_rows_valid = len(eval_rows) == len(sources) and all(
            eval_run.passed and case.split is EvalSplit.TRAIN for eval_run, case in eval_rows
        )
        checks.append(
            GateCheck(
                name="sources_eligible_under_policy"
                if personal_policy
                else "sources_are_passed_train_runs",
                layer="source",
                passed=(
                    graph_valid and bool(source_checks) and all(c.eligible for c in source_checks)
                )
                if personal_policy
                else source_rows_valid,
                actual=sum(c.eligible for c in source_checks)
                if personal_policy
                else len(eval_rows),
                evidence={
                    "source_graph_identity": graph_identity,
                    "sources": [asdict(c) for c in source_checks],
                }
                if personal_policy
                else {},
                threshold=len(sources),
            )
        )
        if personal_policy:
            checks.append(
                GateCheck(
                    name="personal_formal_requires_real_execution",
                    layer="correctness",
                    passed=experiment.config_snapshot.get("provider") not in (None, "mock"),
                    actual=experiment.config_snapshot.get("provider"),
                    evidence={
                        "note": "Mock results do not grant formal adoption to personal candidates"
                    },
                )
            )

        if personal_policy:
            checks.append(
                GateCheck(
                    name="personal_formal_has_successful_treatment",
                    layer="correctness",
                    passed=report.skill_successes > 0 and report.skill_success_rate > 0,
                    actual=report.skill_successes,
                    evidence={
                        "note": "Both arms failing all cases is not evidence of a usable method"
                    },
                )
            )

        invalid_source_artifacts: list[str] = []
        for source in sources:
            if personal_policy:
                check = next((c for c in source_checks if c.source_id == str(source.id)), None)
                if check is None or not check.eligible:
                    invalid_source_artifacts.append(str(source.id))
                continue
            artifact = source_artifacts.get(source.trace_artifact_id)
            if artifact is None:
                invalid_source_artifacts.append(str(source.id))
                continue
            try:
                raw = await self._artifacts.read(artifact.uri)
            except (OSError, ValueError):
                invalid_source_artifacts.append(str(source.id))
                continue
            actual_hash = "sha256:" + hashlib.sha256(raw).hexdigest()
            if (
                actual_hash != artifact.content_hash
                or actual_hash != source.source_trace_hash
                or artifact.type
                != (
                    "learning_source"
                    if source.source_kind == "personal"
                    else "application/vnd.evoagent.skill-source+json"
                )
            ):
                invalid_source_artifacts.append(str(source.id))
        checks.append(
            GateCheck(
                name="source_trace_hashes_match",
                layer="source",
                passed=not invalid_source_artifacts and len(source_artifacts) == len(sources),
                threshold=len(sources),
                actual=len(sources) - len(invalid_source_artifacts),
                evidence={"invalid_source_ids": invalid_source_artifacts},
            )
        )
        unsupported = [
            name for name in definition.validators if not self._validators.supports(name)
        ]
        checks.append(
            GateCheck(
                name="validator_versions_supported",
                layer="static",
                passed=not unsupported,
                actual=unsupported,
            )
        )
        checks.append(
            GateCheck(
                name="all_pairs_comparable",
                layer="correctness",
                passed=report.comparable_pairs == report.pair_count and report.pair_count > 0,
                threshold=report.pair_count,
                actual=report.comparable_pairs,
            )
        )
        checks.append(
            GateCheck(
                name="overall_success_not_regressed",
                layer="correctness",
                passed=report.skill_success_rate >= report.baseline_success_rate,
                threshold=report.baseline_success_rate,
                actual=report.skill_success_rate,
            )
        )
        regressed_families = [
            item.task_family
            for item in report.families
            if item.skill_success_rate < item.baseline_success_rate
        ]
        checks.append(
            GateCheck(
                name="task_families_not_regressed",
                layer="correctness",
                passed=not regressed_families,
                actual=regressed_families,
            )
        )
        checks.append(
            GateCheck(
                name="no_safety_regression",
                layer="safety",
                passed=report.safety_regressions == 0,
                threshold=0,
                actual=report.safety_regressions,
            )
        )
        token_deltas = [item.token_delta for item in report.pairs if item.token_delta is not None]
        checks.append(
            GateCheck(
                name="efficiency_observation",
                layer="efficiency",
                passed=True,
                actual={
                    "comparable_pairs": report.efficiency_comparable_pairs,
                    "mean_token_delta": (
                        sum(token_deltas) / len(token_deltas) if token_deltas else None
                    ),
                },
                evidence={"note": "效率不改善不会覆盖正确性，也不会自动拒绝正确候选"},
            )
        )
        hard_passed = all(item.passed for item in checks if item.layer != "efficiency")
        return GateReport(
            schema_version=2 if personal_policy else 1,
            source_policy_version=FormalSourcePolicy.VERSION if personal_policy else None,
            skill_version_id=version.id,
            experiment_id=report.experiment_id,
            passed=hard_passed,
            checks=tuple(checks),
        )

    async def _personal_sources(self, version, sources):
        from evoagent.core.models import ToolRisk
        from evoagent.db.models import ToolCallRecord, ToolEffectRecord
        from evoagent.tools.base import ToolError

        initial = {}
        try:
            async with asyncio.timeout(10):
                async with self._session_factory() as session:
                    skill = await session.get(SkillRecord, version.skill_id)
                    await SkillAccessPolicy().check(
                        session,
                        version.id,
                        workspace_id=skill.workspace_id,
                        project_id=skill.project_id,
                        source_proofs=initial,
                    )
                    run_ids = {proof.run_id for proof in initial.values()}
                    unsafe_call = await session.scalar(
                        select(ToolCallRecord.id)
                        .where(
                            ToolCallRecord.run_id.in_(run_ids),
                            (ToolCallRecord.risk > ToolRisk.R1)
                            | ToolCallRecord.status.in_(("unknown", "pending", "running")),
                        )
                        .limit(1)
                    )
                    unsafe_effect = await session.scalar(
                        select(ToolEffectRecord.id)
                        .join(ToolCallRecord, ToolCallRecord.id == ToolEffectRecord.tool_call_id)
                        .where(
                            ToolCallRecord.run_id.in_(run_ids),
                            ToolEffectRecord.status.in_(("unknown", "prepared", "executing")),
                        )
                        .limit(1)
                    )
                    if unsafe_call or unsafe_effect:
                        return [], [], False
                from evoagent.config import Settings
                from evoagent.skills.source_verification import SkillSourceVerifier
                from evoagent.skills.trials import TrialScope

                await SkillSourceVerifier(
                    self._session_factory,
                    Settings(_env_file=None),
                    TrialScope(skill.workspace_id, skill.project_id),
                    store=self._artifacts,
                ).verify(version.id)
                policy = FormalSourcePolicy(self._session_factory, self._artifacts)
                graph_sources = list(sources)
                root_artifacts = {source.trace_artifact_id for source in sources}
                async with self._session_factory() as session:
                    for proof in initial.values():
                        if proof.artifact_id in root_artifacts:
                            continue
                        link = await session.scalar(
                            select(SkillSourceRecord)
                            .where(
                                SkillSourceRecord.trace_artifact_id == proof.artifact_id,
                                SkillSourceRecord.source_run_id == proof.run_id,
                                SkillSourceRecord.source_kind == proof.source_kind,
                                SkillSourceRecord.source_trace_hash == proof.content_hash,
                            )
                            .limit(1)
                        )
                        if link is None:
                            return [], [], False
                        graph_sources.append(link)
                if len(graph_sources) > 200:
                    return [], [], False
                checked = [await policy.validate(source) for source in graph_sources]
                async with self._session_factory() as session:
                    current = {}
                    await SkillAccessPolicy().check(
                        session,
                        version.id,
                        workspace_id=skill.workspace_id,
                        project_id=skill.project_id,
                        source_proofs=current,
                    )
                    if current != initial:
                        return [], [], False
                return checked, source_graph_identity(initial), True
        except (OSError, ValueError, ToolError, TimeoutError):
            return [], [], False


class SkillEvaluationService:
    """启动评测，并在实验完成后写入不可替换的 GateReport。"""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        coordinator: EvalCoordinator,
        reports: EvaluationReportService,
        gate: QualityGate,
        artifacts: ArtifactService,
    ) -> None:
        self._session_factory = session_factory
        self._coordinator = coordinator
        self._reports = reports
        self._gate = gate
        self._artifacts = artifacts

    async def start(
        self,
        *,
        skill_version_id: UUID,
        dataset_id: UUID,
        provider: str,
        model: str,
        repeats: int,
        code_version: str,
    ):
        return await self._coordinator.create_experiment(
            dataset_id=dataset_id,
            skill_version_id=skill_version_id,
            provider=provider,
            model=model,
            repeats=repeats,
            code_version=code_version,
        )

    async def finalize(self, experiment_id: UUID) -> tuple[GateReport, str]:
        async with UnitOfWork(self._session_factory) as unit:
            experiment = await unit.evals.get_experiment(experiment_id)
            if experiment.purpose != "formal":
                raise ValueError("formal finalization rejects personal validation experiments")
            if experiment.status is not EvalExperimentStatus.COMPLETED:
                raise ValueError("evaluation experiment has not completed")
            if experiment.gate_report is not None and experiment.gate_report_hash is not None:
                gate = GateReport.model_validate(experiment.gate_report)
                if gate.report_hash() != experiment.gate_report_hash:
                    raise ValueError("stored gate report hash does not match")
                return gate, experiment.gate_report_hash
        report, report_hash, _artifact_id = await self._reports.freeze(
            experiment_id, self._artifacts
        )
        gate = await self._gate.evaluate(report)
        gate_hash = gate.report_hash()
        async with UnitOfWork(self._session_factory) as unit:
            experiment = await unit.evals.get_experiment(experiment_id)
            if experiment.purpose != "formal":
                raise ValueError("formal finalization rejects personal validation experiments")
            if experiment.gate_report is not None:
                raise ValueError("quality gate was concurrently finalized")
            version = await unit.skill_versions.get(report.skill_version_id)
            if version.lifecycle_status is not SkillVersionStatus.EVALUATING:
                raise ValueError("skill version is no longer evaluating")
            target = (
                SkillVersionStatus.REVIEW_REQUIRED if gate.passed else SkillVersionStatus.REJECTED
            )
            ensure_skill_version_transition(version.lifecycle_status, target)
            experiment.gate_report = gate.model_dump(mode="json")
            experiment.gate_report_hash = gate_hash
            version.evaluation_report_hash = report_hash
            version.gate_report_hash = gate_hash
            version.lifecycle_status = target
            await unit.commit()
        return gate, gate_hash
