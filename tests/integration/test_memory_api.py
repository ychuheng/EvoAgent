from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from evoagent.api.app import create_app
from evoagent.config import Settings
from evoagent.db.base import Base
from evoagent.db.models import MemoryVersionRecord
from evoagent.db.session import Database
from evoagent.memory.maintenance import MaintenanceWorker
from evoagent.tasks.lease import JobLeaseManager, TaskExecutionResult
from evoagent.tasks.state_machine import PersistentRunStatus
from evoagent.trace.artifacts import LocalArtifactStore


async def test_memory_api_confirm_query_revoke_archive_and_erase(tmp_path):
    db = Database(f"sqlite+aiosqlite:///{tmp_path / 'api.db'}")
    async with db.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    settings = Settings(workspace=tmp_path / "workspace")
    app = create_app(settings, database=db)
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client,
    ):
        sid = (await client.post("/api/v1/sessions", json={"title": "记忆"})).json()["id"]
        await client.post("/api/v1/tasks", json={"session_id": sid, "goal": "请记住：以后都用中文"})
        manager = JobLeaseManager(db.session_factory, lease_seconds=60)
        lease = await manager.claim_next("api-test")
        await manager.finalize(
            lease, TaskExecutionResult(status=PersistentRunStatus.COMPLETED, final_answer="好")
        )
        messages = (await client.get(f"/api/v1/sessions/{sid}/messages")).json()
        assert len(messages) == 2
        endpoint = f"/api/v1/sessions/{sid}/memory-extractions/{messages[0]['id']}"
        first = (await client.post(endpoint)).json()[0]
        again = (await client.post(endpoint)).json()[0]
        assert first["version_id"] == again["version_id"]
        path = f"/api/v1/sessions/{sid}/memories/{first['version_id']}/decision"
        confirmed = await client.post(path, json={"action": "confirm", "expected_lock_version": 0})
        assert confirmed.status_code == 200 and confirmed.json()["status"] == "confirmed"
        assert (
            len(
                (
                    await client.get(f"/api/v1/sessions/{sid}/memories", params={"query": "中文"})
                ).json()
            )
            == 1
        )
        conflict = await client.post(path, json={"action": "revoke", "expected_lock_version": 0})
        assert conflict.status_code == 409
        archive = await client.post(f"/api/v1/sessions/{sid}/archives")
        assert archive.status_code == 202
        worker = MaintenanceWorker(db.session_factory, LocalArtifactStore(settings.artifact_root))
        await worker.run_once()
        revoked = await client.post(path, json={"action": "erase", "expected_lock_version": 1})
        assert revoked.status_code == 200
        erase_job_id = revoked.json()["maintenance_job_id"]
        assert (await client.get(f"/api/v1/maintenance-jobs/{erase_job_id}")).json()[
            "status"
        ] == "pending"
        assert (
            await client.get(f"/api/v1/sessions/{sid}/memories", params={"query": "中文"})
        ).json() == []
        await worker.run_once()
        assert (await client.get(f"/api/v1/maintenance-jobs/{erase_job_id}")).json()[
            "status"
        ] == "completed"
        async with db.session_factory() as session:
            version = await session.scalar(select(MemoryVersionRecord))
            assert version.content is None and version.status == "erased"
        archives = (await client.get(f"/api/v1/sessions/{sid}/archives")).json()
        assert archives[0]["summary"] is None
    await db.dispose()
