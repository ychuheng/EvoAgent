"""分层 QualityGate 与 SkillVersion 评测生命周期推进。"""

import hashlib
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from evoagent.db.models import (
    ArtifactRecord,
    EvalCaseRecord,
    EvalRunRecord,
    SkillSourceRecord,
)
from evoagent.db.unit_of_work import UnitOfWork
from evoagent.evals.coordinator import EvalCoordinator
from evoagent.evals.lifecycle import EvalExperimentStatus, EvalSplit
from evoagent.evals.metrics import EvaluationReport, EvaluationReportService
from evoagent.evals.validators.base import ValidatorRegistry
from evoagent.skills.canonical import content_hash
from evoagent.skills.lifecycle import (
    SkillVersionStatus,
    ensure_skill_version_transition,
)
from evoagent.skills.schema import SkillDefinition
from evoagent.skills.validation import SkillDefinitionValidator
from evoagent.trace.artifacts import ArtifactService


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

    def report_hash(self) -> str:
        return content_hash(self.model_dump(mode="json"))


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
        async with UnitOfWork(self._session_factory) as unit:
            version = await unit.skill_versions.get(report.skill_version_id)
            experiment = await unit.evals.get_experiment(report.experiment_id)
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
        independent_sources = len({item.source_run_id for item in sources})
        checks.append(
            GateCheck(
                name="minimum_independent_sources",
                layer="source",
                passed=independent_sources >= self._minimum_sources,
                threshold=self._minimum_sources,
                actual=independent_sources,
                evidence={"source_ids": [str(item.id) for item in sources]},
            )
        )
        source_rows_valid = len(eval_rows) == len(sources) and all(
            eval_run.passed and case.split is EvalSplit.TRAIN for eval_run, case in eval_rows
        )
        checks.append(
            GateCheck(
                name="sources_are_passed_train_runs",
                layer="source",
                passed=source_rows_valid,
                actual=len(eval_rows),
                threshold=len(sources),
            )
        )
        invalid_source_artifacts: list[str] = []
        for source in sources:
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
                or artifact.type != "application/vnd.evoagent.skill-source+json"
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
            skill_version_id=version.id,
            experiment_id=report.experiment_id,
            passed=hard_passed,
            checks=tuple(checks),
        )


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
