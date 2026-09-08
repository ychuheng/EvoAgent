"""评测数据集、实验、配对明细和报告 HTTP 入口。"""

from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from evoagent.db.models import (
    EvalCaseRecord,
    EvalDatasetRecord,
    EvalExperimentRecord,
    EvalRunRecord,
)
from evoagent.evals.coordinator import EvalCoordinator
from evoagent.evals.datasets import EvalDatasetService
from evoagent.evals.gates import GateReport, QualityGate, SkillEvaluationService
from evoagent.evals.lifecycle import EvalExperimentStatus
from evoagent.evals.metrics import EvaluationReportService
from evoagent.evals.schema import EvalDatasetDefinition
from evoagent.evals.validators import default_validator_registry
from evoagent.skills.validation import SkillDefinitionValidator
from evoagent.tools.registry import ToolRegistry
from evoagent.trace.artifacts import ArtifactService, LocalArtifactStore

router = APIRouter(tags=["evaluations"])


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EvaluationStartRequest(StrictModel):
    dataset_id: UUID
    provider: str | None = Field(default=None, min_length=1, max_length=64)
    model: str | None = Field(default=None, min_length=1, max_length=256)
    repeats: int | None = Field(default=None, ge=1, le=100)


def _components(request: Request):
    settings = request.app.state.settings
    database = request.app.state.database
    registry = request.app.state.skill_tool_registry or ToolRegistry()
    validators = default_validator_registry()
    definition_validator = SkillDefinitionValidator(
        registry,
        allowed_tools=frozenset(settings.skill_allowed_tools),
        max_steps=settings.skill_max_steps,
        max_risk=settings.skill_max_effective_risk,
        supported_schema_version=settings.skill_schema_version,
    )
    coordinator = EvalCoordinator(
        database.session_factory,
        validators,
        lease_seconds=settings.eval_lease_seconds,
    )
    reports = EvaluationReportService(database.session_factory)
    artifacts = ArtifactService(
        LocalArtifactStore(settings.artifact_root), database.session_factory
    )
    gate = QualityGate(
        database.session_factory,
        definition_validator,
        validators,
        artifacts,
        minimum_sources=settings.skill_min_sources,
    )
    return (
        coordinator,
        reports,
        SkillEvaluationService(database.session_factory, coordinator, reports, gate, artifacts),
    )


@router.post("/eval-datasets/import", status_code=status.HTTP_201_CREATED)
async def import_dataset(definition: EvalDatasetDefinition, request: Request) -> dict[str, Any]:
    try:
        dataset = await EvalDatasetService(
            request.app.state.database.session_factory
        ).import_definition(definition)
    except ValueError as error:
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error
    return {
        "id": str(dataset.id),
        "name": dataset.name,
        "version": dataset.version,
        "content_hash": dataset.content_hash,
        "status": dataset.status.value,
    }


@router.get("/eval-datasets")
async def list_datasets(request: Request) -> tuple[dict[str, Any], ...]:
    database = request.app.state.database
    async with database.session_factory() as session:
        rows = tuple(
            await session.scalars(
                select(EvalDatasetRecord).order_by(
                    EvalDatasetRecord.name, EvalDatasetRecord.version.desc()
                )
            )
        )
    return tuple(
        {
            "id": str(item.id),
            "name": item.name,
            "version": item.version,
            "content_hash": item.content_hash,
            "status": item.status.value,
        }
        for item in rows
    )


@router.post("/eval-datasets/{dataset_id}/freeze")
async def freeze_dataset(dataset_id: UUID, request: Request) -> dict[str, Any]:
    try:
        dataset = await EvalDatasetService(request.app.state.database.session_factory).freeze(
            dataset_id
        )
    except ValueError as error:
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error
    return {
        "id": str(dataset.id),
        "name": dataset.name,
        "version": dataset.version,
        "content_hash": dataset.content_hash,
        "status": dataset.status.value,
    }


@router.post(
    "/skill-versions/{version_id}/evaluations",
    status_code=status.HTTP_202_ACCEPTED,
)
async def start_evaluation(
    version_id: UUID, payload: EvaluationStartRequest, request: Request
) -> dict[str, Any]:
    settings = request.app.state.settings
    _coordinator, _reports, service = _components(request)
    try:
        experiment = await service.start(
            skill_version_id=version_id,
            dataset_id=payload.dataset_id,
            provider=payload.provider or settings.provider.value,
            model=payload.model or settings.model or "mock-model",
            repeats=payload.repeats or settings.eval_repeats,
            code_version=settings.code_version,
        )
    except ValueError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from error
    return {"experiment_id": str(experiment.id), "status": experiment.status.value}


@router.get("/eval-experiments/{experiment_id}")
async def get_experiment(experiment_id: UUID, request: Request) -> dict[str, Any]:
    database = request.app.state.database
    async with database.session_factory() as session:
        experiment = await session.get(EvalExperimentRecord, experiment_id)
    if experiment is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "evaluation experiment not found")
    return {
        "id": str(experiment.id),
        "kind": experiment.kind.value,
        "skill_version_id": (
            str(experiment.skill_version_id) if experiment.skill_version_id else None
        ),
        "dataset_id": str(experiment.dataset_id),
        "status": experiment.status.value,
        "config_hash": experiment.config_hash,
        "report_hash": experiment.report_hash,
        "gate_report_hash": experiment.gate_report_hash,
        "created_at": experiment.created_at,
    }


@router.get("/eval-experiments/{experiment_id}/pairs")
async def get_pairs(experiment_id: UUID, request: Request) -> tuple[dict[str, Any], ...]:
    database = request.app.state.database
    async with database.session_factory() as session:
        if await session.get(EvalExperimentRecord, experiment_id) is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "evaluation experiment not found")
        rows = tuple(
            await session.execute(
                select(EvalRunRecord, EvalCaseRecord)
                .join(EvalCaseRecord, EvalCaseRecord.id == EvalRunRecord.eval_case_id)
                .where(EvalRunRecord.experiment_id == experiment_id)
                .order_by(
                    EvalCaseRecord.case_key,
                    EvalRunRecord.repeat_index,
                    EvalRunRecord.mode,
                )
            )
        )
    return tuple(
        {
            "id": str(item.id),
            "case_key": case.case_key,
            "task_family": case.task_family,
            "mode": item.mode.value,
            "repeat_index": item.repeat_index,
            "task_id": str(item.task_id),
            "run_id": str(item.run_id),
            "passed": item.passed,
            "comparable": item.comparable,
            "metrics": item.metrics,
            "validation_results": item.validation_results,
        }
        for item, case in rows
    )


@router.get("/eval-experiments/{experiment_id}/report")
async def get_report(experiment_id: UUID, request: Request) -> dict[str, Any]:
    _coordinator, reports, _service = _components(request)
    database = request.app.state.database
    async with database.session_factory() as session:
        experiment = await session.get(EvalExperimentRecord, experiment_id)
    if experiment is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "evaluation experiment not found")
    if experiment.status is not EvalExperimentStatus.COMPLETED:
        raise HTTPException(status.HTTP_409_CONFLICT, "evaluation experiment is not completed")
    try:
        report = await reports.build(experiment_id)
    except ValueError as error:
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error
    gate = (
        GateReport.model_validate(experiment.gate_report)
        if experiment.gate_report is not None
        else None
    )
    return {
        "report": report.model_dump(mode="json"),
        "report_hash": experiment.report_hash or report.report_hash(),
        "gate_report": gate.model_dump(mode="json") if gate is not None else None,
        "gate_report_hash": experiment.gate_report_hash,
    }


@router.post("/eval-experiments/{experiment_id}/finalize")
async def finalize_experiment(experiment_id: UUID, request: Request) -> dict[str, Any]:
    """显式冻结报告并推进门禁，避免只读 GET 请求偷偷修改状态。"""

    _coordinator, reports, service = _components(request)
    try:
        gate, gate_hash = await service.finalize(experiment_id)
        report = await reports.build(experiment_id)
    except (ValueError, LookupError) as error:
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error
    return {
        "report": report.model_dump(mode="json"),
        "report_hash": report.report_hash(),
        "gate_report": gate.model_dump(mode="json"),
        "gate_report_hash": gate_hash,
    }


@router.post("/eval-experiments/{experiment_id}/cancel")
async def cancel_experiment(experiment_id: UUID, request: Request) -> dict[str, Any]:
    coordinator, _reports, _service = _components(request)
    try:
        experiment = await coordinator.cancel(experiment_id)
    except (ValueError, LookupError) as error:
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error
    return {"experiment_id": str(experiment.id), "status": experiment.status.value}
