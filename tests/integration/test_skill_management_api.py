from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from evoagent.api.app import create_app
from evoagent.config import Settings
from evoagent.core.models import ToolRisk
from evoagent.db.base import Base
from evoagent.db.models import (
    EvalDatasetRecord,
    EvalExperimentRecord,
    SkillRecord,
    SkillVersionRecord,
)
from evoagent.db.session import Database
from evoagent.evals.gates import GateReport
from evoagent.evals.lifecycle import DatasetStatus, EvalExperimentKind, EvalExperimentStatus
from evoagent.runtime.run_config import sha256_text
from evoagent.skills.canonical import content_hash
from evoagent.skills.lifecycle import SkillVersionStatus
from evoagent.skills.schema import SkillDefinition, SkillPreconditions, ToolStep
from evoagent.tools.builtin.calculator import CalculatorTool
from evoagent.tools.registry import ToolRegistry


def make_definition(expression: str) -> SkillDefinition:
    return SkillDefinition(
        name="math_report",
        description="生成数学报告",
        triggers=("数学",),
        preconditions=SkillPreconditions(
            allowed_tools=("calculator",), max_effective_risk=ToolRisk.R0
        ),
        steps=(ToolStep(id="calculate", tool="calculator", args={"expression": expression}),),
        success_criteria=("给出结果",),
        validators=("run_completed",),
    )


@asynccontextmanager
async def management_client(tmp_path: Path) -> AsyncIterator[tuple[AsyncClient, Database]]:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'management.db'}")
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'management.db'}",
        workspace=tmp_path / "workspace",
        skill_allowed_tools=("calculator",),
    )
    app = create_app(
        settings,
        database=database,
        skill_tool_registry=ToolRegistry((CalculatorTool(),)),
    )
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client,
    ):
        yield client, database
    await database.dispose()


async def seed_versions(database: Database):
    async with database.session_factory() as session:
        dataset = EvalDatasetRecord(
            name="management",
            version=1,
            content_hash=sha256_text("dataset"),
            status=DatasetStatus.FROZEN,
        )
        skill = SkillRecord(
            name="math_report",
            slug="math_report",
            description="生成数学报告",
        )
        session.add_all((dataset, skill))
        await session.flush()
        versions = []
        for number, expression, state in (
            (1, "1+1", SkillVersionStatus.ACTIVE),
            (2, "2+2", SkillVersionStatus.REVIEW_REQUIRED),
        ):
            definition = make_definition(expression)
            version = SkillVersionRecord(
                skill_id=skill.id,
                parent_version_id=versions[-1].id if versions else None,
                version=number,
                schema_version=1,
                definition=definition.model_dump(mode="json"),
                content_hash=content_hash(definition.model_dump(mode="json")),
                extraction_key=sha256_text(f"management-{number}"),
                lifecycle_status=state,
            )
            session.add(version)
            await session.flush()
            experiment = EvalExperimentRecord(
                kind=EvalExperimentKind.SKILL_COMPARISON,
                skill_version_id=version.id,
                dataset_id=dataset.id,
                status=EvalExperimentStatus.COMPLETED,
                config_snapshot={"version": number},
                config_hash=sha256_text(f"config-{number}"),
            )
            session.add(experiment)
            await session.flush()
            gate = GateReport(
                skill_version_id=version.id,
                experiment_id=experiment.id,
                passed=True,
                checks=(),
            )
            version.gate_report_hash = gate.report_hash()
            experiment.gate_report = gate.model_dump(mode="json")
            experiment.gate_report_hash = gate.report_hash()
            versions.append(version)
        skill.active_version_id = versions[0].id
        await session.commit()
        return skill, versions


@pytest.mark.asyncio
async def test_skill_query_publish_conflict_and_rollback_api(tmp_path: Path) -> None:
    async with management_client(tmp_path) as (client, database):
        skill, versions = await seed_versions(database)
        listed = await client.get("/api/v1/skills")
        detail = await client.get(f"/api/v1/skills/{skill.id}")
        version = await client.get(f"/api/v1/skill-versions/{versions[1].id}")
        diff = await client.get(
            f"/api/v1/skill-versions/{versions[1].id}/diff",
            params={"against": str(versions[0].id)},
        )
        assert listed.status_code == detail.status_code == version.status_code == 200
        assert len(detail.json()["versions"]) == 2
        assert diff.json()["changes"]

        approved = await client.post(
            f"/api/v1/skill-versions/{versions[1].id}/review",
            json={
                "action": "approve",
                "expected_lock_version": 0,
                "reviewer": "api-test",
                "reason": "门禁已经通过",
            },
        )
        assert approved.status_code == 200
        assert approved.json()["active_version_id"] == str(versions[1].id)

        stale = await client.post(
            f"/api/v1/skills/{skill.id}/disable",
            json={
                "expected_lock_version": 0,
                "reviewer": "api-test",
                "reason": "测试旧 lock_version",
            },
        )
        assert stale.status_code == 409
        assert stale.json()["error"]["code"] == "version_conflict"

        rollback = await client.post(
            f"/api/v1/skills/{skill.id}/rollback",
            json={
                "target_version_id": str(versions[0].id),
                "expected_lock_version": 1,
                "reviewer": "api-test",
                "reason": "回滚到第一版",
            },
        )
        assert rollback.status_code == 200
        assert rollback.json()["active_version_id"] == str(versions[0].id)


@pytest.mark.asyncio
async def test_dataset_api_never_returns_private_validators(tmp_path: Path) -> None:
    async with management_client(tmp_path) as (client, _database):
        imported = await client.post(
            "/api/v1/eval-datasets/import",
            json={
                "name": "api_dataset",
                "version": 1,
                "cases": [
                    {
                        "case_key": "hidden_case",
                        "task_family": "safety",
                        "split": "holdout",
                        "public_input": {"goal": "公开任务"},
                        "private_validators": [{"name": "run_completed"}],
                        "risk_profile": {},
                    }
                ],
            },
        )
        frozen = await client.post(f"/api/v1/eval-datasets/{imported.json()['id']}/freeze")
        listed = await client.get("/api/v1/eval-datasets")
        assert imported.status_code == 201
        assert frozen.json()["status"] == "frozen"
        assert "private_validators" not in listed.text
