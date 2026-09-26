import asyncio
import os
import sys
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from evoagent.api.app import create_app
from evoagent.config import Settings
from evoagent.db.base import Base
from evoagent.db.session import Database


@pytest.mark.parametrize("mode", ["readonly", "committed", "unknown"])
async def test_api_task_survives_killed_worker(tmp_path, mode):
    url = f"sqlite+aiosqlite:///{tmp_path / 'process.db'}"
    settings = Settings(_env_file=None, database_url=url, workspace=tmp_path / "workspace")
    database = Database(url)
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    app = create_app(settings, database=database)
    processes = []

    async def start(mode):
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            str(Path(__file__).with_name("worker_crash_fixture.py")),
            str(tmp_path),
            mode,
            env={**os.environ, "PYTHONPATH": str(Path("src").resolve())},
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        processes.append(process)
        return process

    try:
        async with (
            app.router.lifespan_context(app),
            AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client,
        ):
            session = (await client.post("/api/v1/sessions", json={"title": mode})).json()
            response = await client.post(
                "/api/v1/tasks",
                json={
                    "session_id": session["id"],
                    "goal": "离线搜索并生成 Markdown 报告",
                },
            )
            assert response.status_code == 202
            task = response.json()
            first = await start(mode)
            async with asyncio.timeout(20):
                while not (tmp_path / "ready").exists():
                    if first.returncode is not None:
                        _, error = await first.communicate()
                        pytest.fail(error.decode(errors="replace"))
                    await asyncio.sleep(0.05)
            first.kill()
            await first.communicate()
            await asyncio.sleep(1.1)
            replacement = await start("normal")
            _, error = await asyncio.wait_for(replacement.communicate(), timeout=20)
            assert replacement.returncode == 0, error.decode(errors="replace")
            stored = (await client.get(f"/api/v1/tasks/{task['id']}")).json()
            trace = (await client.get(f"/api/v1/runs/{task['latest_run']['id']}/trace")).json()
            assert stored["status"] == ("waiting_user" if mode == "unknown" else "completed")
            assert (tmp_path / "invocations").read_text().splitlines() == ["write"]
            effects = trace["tool_effects"]
            assert len(effects) == 1
            assert effects[0]["status"] == ("unknown" if mode == "unknown" else "committed")
            if mode == "readonly":
                assert len([c for c in trace["tool_calls"] if c["tool_name"] == "web_search"]) == 1
    finally:
        for process in processes:
            if process.returncode is None:
                process.kill()
                await process.communicate()
        await database.dispose()


@pytest.mark.postgres
@pytest.mark.parametrize("mode", ["readonly", "committed", "unknown"])
async def test_postgres_standby_worker_recovers_active_crash(tmp_path, mode):
    """A second live worker must take over an expired PostgreSQL lease."""
    url = os.getenv("EVOAGENT_TEST_DATABASE_URL")
    if not url:
        pytest.skip("EVOAGENT_TEST_DATABASE_URL is not configured")
    settings = Settings(_env_file=None, database_url=url, workspace=tmp_path / "workspace")
    database = Database(url)
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    app = create_app(settings, database=database)
    processes = []

    async def start(worker_mode):
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            str(Path(__file__).with_name("worker_crash_fixture.py")),
            str(tmp_path),
            worker_mode,
            env={
                **os.environ,
                "PYTHONPATH": str(Path("src").resolve()),
                "EVOAGENT_WORKER_CRASH_DATABASE_URL": url,
            },
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        processes.append(process)
        return process

    try:
        async with (
            app.router.lifespan_context(app),
            AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client,
        ):
            session = (await client.post("/api/v1/sessions", json={"title": mode})).json()
            response = await client.post(
                "/api/v1/tasks",
                json={"session_id": session["id"], "goal": "离线搜索并生成 Markdown 报告"},
            )
            assert response.status_code == 202
            task = response.json()
            first = await start(mode)
            async with asyncio.timeout(20):
                while not (tmp_path / "ready").exists():
                    if first.returncode is not None:
                        _, error = await first.communicate()
                        pytest.fail(error.decode(errors="replace"))
                    await asyncio.sleep(0.05)
            standby = await start("standby")
            assert standby.returncode is None
            first.kill()
            await first.communicate()
            expected = "waiting_user" if mode == "unknown" else "completed"
            async with asyncio.timeout(25):
                while True:
                    stored = (await client.get(f"/api/v1/tasks/{task['id']}")).json()
                    if stored["status"] == expected:
                        break
                    if standby.returncode is not None:
                        _, error = await standby.communicate()
                        pytest.fail(error.decode(errors="replace"))
                    await asyncio.sleep(0.1)
            trace = (await client.get(f"/api/v1/runs/{task['latest_run']['id']}/trace")).json()
            assert stored["status"] == expected
            assert (tmp_path / "invocations").read_text().splitlines() == ["write"]
            assert len(trace["tool_effects"]) == 1
            assert trace["tool_effects"][0]["status"] == (
                "unknown" if mode == "unknown" else "committed"
            )
            if mode == "unknown":
                assert any(approval["status"] == "pending" for approval in trace["approvals"])
    finally:
        for process in processes:
            if process.returncode is None:
                process.kill()
                await process.communicate()
        async with database.engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)
        await database.dispose()
