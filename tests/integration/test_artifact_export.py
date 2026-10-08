"""M5 产物出口的集成测试（实施计划 §10 F-04）。

覆盖验收要求：用户能拿到报告/整理结果并核对内容；链接权限与路径不越界。
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from evoagent.api.app import create_app
from evoagent.config import Settings
from evoagent.db.base import Base
from evoagent.db.session import Database
from evoagent.tasks.lease import JobLeaseManager, TaskExecutionResult
from evoagent.tasks.service import TaskService
from evoagent.tasks.state_machine import PersistentRunStatus
from evoagent.trace.artifacts import ArtifactService, LocalArtifactStore


@asynccontextmanager
async def artifact_client(tmp_path: Path) -> AsyncIterator[tuple[AsyncClient, Database, str]]:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'artifact.db'}")
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'artifact.db'}",
        workspace=tmp_path / "workspace",
        artifact_root=tmp_path / "workspace" / "artifacts",
        artifact_download_max_bytes=4_096,
    )
    service = TaskService(database.session_factory)
    session = await service.create_session("产物出口")
    aggregate = await service.create_task(
        session_id=session.id, goal="生成报告", provider="mock", model="mock-model"
    )
    leases = JobLeaseManager(database.session_factory, lease_seconds=60)
    lease = await leases.claim_next("worker-artifact")
    assert lease is not None
    await leases.finalize(
        lease, TaskExecutionResult(status=PersistentRunStatus.COMPLETED, final_answer="完成")
    )

    artifacts = ArtifactService(
        LocalArtifactStore(settings.artifact_root), database.session_factory
    )
    report = await artifacts.create(
        run_id=aggregate.run.id,
        name="report.md",
        content="# 报告\n\n结论：可用。\n".encode(),
        artifact_type="report",
        attributes={"content_type": "text/markdown"},
    )
    app = create_app(settings, database=database)
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client,
    ):
        yield client, database, str(report.id)
    await database.dispose()


@pytest.mark.asyncio
async def test_artifact_preview_and_download_match_registered_hash(tmp_path: Path) -> None:
    async with artifact_client(tmp_path) as (client, _database, artifact_id):
        detail = await client.get(f"/api/v1/artifacts/{artifact_id}")
        assert detail.status_code == 200
        body = detail.json()
        assert body["name"] == "report.md"
        assert body["content_type"] == "text/markdown"
        assert body["size_bytes"] > 0
        assert "# 报告" in body["preview"]
        assert body["preview_truncated"] is False
        assert body["download_url"] == f"/api/v1/artifacts/{artifact_id}/download"

        download = await client.get(f"/api/v1/artifacts/{artifact_id}/download")
        assert download.status_code == 200
        assert download.headers["x-content-sha256"] == body["content_hash"]
        assert "report.md" in download.headers["content-disposition"]
        assert download.content.decode() == "# 报告\n\n结论：可用。\n"


@pytest.mark.asyncio
async def test_artifact_preview_truncates_and_points_at_download(tmp_path: Path) -> None:
    async with artifact_client(tmp_path) as (client, database, artifact_id):
        detail = await client.get(f"/api/v1/artifacts/{artifact_id}", params={"preview_bytes": 256})
        body = detail.json()
        assert body["preview"] == "# 报告\n\n结论：可用。\n"
        assert body["preview_truncated"] is False

        # 上限远大于内容时不会截断；下限保护见下一条。
        tiny = await client.get(f"/api/v1/artifacts/{artifact_id}", params={"preview_bytes": 8})
        assert tiny.status_code == 422

        # 内容超过上限时预览截断，并指向下载。
        artifacts = ArtifactService(
            LocalArtifactStore(tmp_path / "workspace" / "artifacts"), database.session_factory
        )
        from sqlalchemy import select

        from evoagent.db.models import RunRecord

        async with database.session_factory() as session:
            run = await session.scalar(select(RunRecord))
        long_text = await artifacts.create(
            run_id=run.id,
            name="long.md",
            content=("# 很长的报告\n" * 100).encode(),
            artifact_type="report",
            attributes={"content_type": "text/markdown"},
        )
        truncated = await client.get(
            f"/api/v1/artifacts/{long_text.id}", params={"preview_bytes": 256}
        )
        assert truncated.json()["preview_truncated"] is True
        assert "下载" in truncated.json()["note"]


@pytest.mark.asyncio
async def test_artifact_download_refuses_oversized_and_unknown(tmp_path: Path) -> None:
    async with artifact_client(tmp_path) as (client, database, _artifact_id):
        # 新登记一个超过上限的产物。
        from sqlalchemy import select

        from evoagent.db.models import RunRecord

        async with database.session_factory() as session:
            run = await session.scalar(select(RunRecord))
        artifacts = ArtifactService(
            LocalArtifactStore(tmp_path / "workspace" / "artifacts"), database.session_factory
        )
        big = await artifacts.create(
            run_id=run.id,
            name="big.txt",
            content=b"x" * 8_192,
            artifact_type="report",
            attributes={"content_type": "text/plain"},
        )

        oversized = await client.get(f"/api/v1/artifacts/{big.id}/download")
        assert oversized.status_code == 413
        assert "下载上限" in oversized.json()["detail"]

        missing = await client.get(f"/api/v1/artifacts/{uuid4()}")
        assert missing.status_code == 404


@pytest.mark.asyncio
async def test_artifact_download_detects_tampered_content(tmp_path: Path) -> None:
    """存储内容与登记哈希不一致时必须拒绝导出，不能当"可核对产物"发出去。"""

    async with artifact_client(tmp_path) as (client, _database, artifact_id):
        stored = tmp_path / "workspace" / "artifacts"
        target = next(stored.rglob("report.md"))
        target.write_text("# 被替换的内容\n", encoding="utf-8")

        response = await client.get(f"/api/v1/artifacts/{artifact_id}/download")
        assert response.status_code == 409
        assert "不一致" in response.json()["detail"]


async def _quarantine(database: Database, artifact_id: str) -> str:
    """把产物置为"隔离中"（上一版策略），返回其 content_hash。"""

    from uuid import UUID

    from evoagent.db.models import ArtifactRecord
    from evoagent.privacy.redaction import POLICY_VERSION

    async with database.session_factory() as session:
        record = await session.get(ArtifactRecord, UUID(artifact_id))
        record.redaction_status = "quarantined"
        record.redaction_policy_version = POLICY_VERSION - 1
        record.redaction_checked_hash = None
        await session.commit()
        return record.content_hash


@pytest.mark.asyncio
async def test_quarantine_review_endpoint_clears_idempotently_and_maps_conflicts(
    tmp_path: Path,
) -> None:
    """§2.4 的 HTTP 出口：通过才解封、重试幂等、过期条件 409、身份不可伪造。"""

    from uuid import UUID

    from sqlalchemy import select

    from evoagent.db.models import ArtifactRecord, RunEventRecord
    from evoagent.privacy.artifact_access import REVIEW_CLEARED_EVENT, REVIEW_REQUESTED_EVENT
    from evoagent.privacy.redaction import POLICY_VERSION

    async with artifact_client(tmp_path) as (client, database, artifact_id):
        content_hash = await _quarantine(database, artifact_id)
        body = {
            "reason": "本地核对后确认是误报，规则已修正并升版",
            "expected_policy_version": POLICY_VERSION - 1,
            "expected_content_hash": content_hash,
            "client_request_id": "req-api-1",
            # 伪造身份：请求体里的 actor 必须被忽略
            "actor": "attacker",
        }

        cleared = await client.post(f"/api/v1/artifacts/{artifact_id}/quarantine-review", json=body)
        assert cleared.status_code == 200
        assert cleared.json()["outcome"] == "cleared"
        assert cleared.json()["policy_version"] == POLICY_VERSION
        assert cleared.json()["replayed"] is False

        again = await client.post(f"/api/v1/artifacts/{artifact_id}/quarantine-review", json=body)
        assert again.status_code == 200
        assert again.json()["replayed"] is True

        async with database.session_factory() as session:
            events = tuple(
                await session.scalars(
                    select(RunEventRecord)
                    .where(
                        RunEventRecord.event_type.in_(
                            (REVIEW_REQUESTED_EVENT, REVIEW_CLEARED_EVENT)
                        )
                    )
                    .order_by(RunEventRecord.sequence)
                )
            )
        assert [event.event_type for event in events] == [
            REVIEW_REQUESTED_EVENT,
            REVIEW_CLEARED_EVENT,
        ]
        assert {event.payload["actor"] for event in events} == {"human"}

        stale = await client.post(
            f"/api/v1/artifacts/{artifact_id}/quarantine-review",
            json={
                **body,
                "expected_content_hash": "sha256:" + "0" * 64,
                "client_request_id": "req-api-2",
            },
        )
        assert stale.status_code == 409

        missing = await client.post(f"/api/v1/artifacts/{uuid4()}/quarantine-review", json=body)
        assert missing.status_code == 404

        incomplete = await client.post(
            f"/api/v1/artifacts/{artifact_id}/quarantine-review",
            json={
                "expected_policy_version": 0,
                "expected_content_hash": "sha256:" + "0" * 64,
                "client_request_id": "req-api-3",
            },
        )
        assert incomplete.status_code == 422

        async with database.session_factory() as session:
            record = await session.get(ArtifactRecord, UUID(artifact_id))
        assert record.redaction_status == "verified"
        assert record.content_hash == content_hash  # bytes/hash 不因解封重算
