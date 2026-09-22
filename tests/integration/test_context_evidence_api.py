from uuid import uuid4

from httpx import ASGITransport, AsyncClient

from evoagent.api.app import create_app
from evoagent.config import Settings
from evoagent.db.base import Base
from evoagent.db.models import ArtifactRecord, ContextRevisionRecord, RunRecord
from evoagent.db.session import Database
from evoagent.tasks.service import TaskService


async def test_context_evidence_is_ordered_and_excludes_messages_secrets_and_artifact_uri(tmp_path):
    db = Database(f"sqlite+aiosqlite:///{tmp_path / 'context.db'}")
    async with db.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    service = TaskService(db.session_factory)
    scope = await service.create_session("context evidence")
    task = await service.create_task(
        session_id=scope.id, goal="private-message-sentinel", provider="mock", model="mock"
    )
    app = create_app(Settings(workspace=tmp_path), database=db)
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client,
    ):
        path = f"/api/v1/runs/{task.run.id}/context"
        empty = (await client.get(path)).json()
        assert empty["policy"] == {} and empty["revisions"] == []
        async with db.session_factory() as session:
            run = await session.get(RunRecord, task.run.id)
            run.config_snapshot = {
                "api_key": "private-credential-sentinel",
                "context_policy": {"mode": "bounded", "budget": {"context_window": 4096}},
                "max_output_tokens": 128,
            }
            artifact = ArtifactRecord(
                run_id=run.id,
                type="context_source",
                uri="private-artifact-path",
                content_hash="sha256:artifact",
                size_bytes=10,
                attributes={},
            )
            session.add(artifact)
            await session.flush()
            parent = None
            for number in (1, 2):
                revision = ContextRevisionRecord(
                    id=uuid4(),
                    run_id=run.id,
                    revision=number,
                    parent_id=parent,
                    dedupe_key=f"sha256:{number}",
                    input_hash=f"sha256:input{number}",
                    policy_hash="sha256:policy",
                    artifact_id=artifact.id,
                    summary={"content": "private-summary-sentinel"},
                    estimate={"before": 5000, "after": 3000},
                )
                session.add(revision)
                await session.flush()
                parent = revision.id
            await session.commit()
        response = await client.get(path)
        assert response.status_code == 200
        data = response.json()
        assert data["policy"]["budget"]["context_window"] == 4096
        assert data["max_output_tokens"] == 128
        assert [r["revision"] for r in data["revisions"]] == [1, 2]
        assert data["revisions"][1]["parent_id"] == data["revisions"][0]["id"]
        assert data["revisions"][0]["estimate"] == {"before": 5000, "after": 3000}
        assert "private-" not in response.text
        assert (await client.get(f"/api/v1/runs/{uuid4()}/context")).status_code == 404
    await db.dispose()
