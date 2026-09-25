"""Exercise real worker retrieval across two logical workspaces.

Run only against the local acceptance deployment. Creates synthetic, public facts.
"""

import argparse
import asyncio
import json
from time import monotonic

import httpx

from evoagent.db.models import DEFAULT_WORKSPACE_ID, SessionRecord, WorkspaceRecord
from evoagent.db.session import Database


async def wait_task(client, task_id, timeout=90):
    deadline = monotonic() + timeout
    while monotonic() < deadline:
        task = (await client.get(f"/tasks/{task_id}")).raise_for_status().json()
        if task["status"] in {"completed", "failed", "cancelled"}:
            return task
        await asyncio.sleep(1)
    raise TimeoutError(task_id)


async def submit(client, session_id, goal):
    response = await client.post("/tasks", json={"session_id": session_id, "goal": goal})
    task = response.raise_for_status().json()
    return await wait_task(client, task["id"])


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--api", default="http://127.0.0.1:18000/api/v1")
    args = parser.parse_args()
    if "127.0.0.1" not in args.api:
        raise ValueError("acceptance API must be loopback")
    db = Database(args.database_url)
    try:
        async with db.session_factory() as transaction:
            workspace = WorkspaceRecord(name="Acceptance isolated workspace")
            transaction.add(workspace)
            await transaction.flush()
            isolated = SessionRecord(title="Acceptance isolated memory", workspace_id=workspace.id)
            transaction.add(isolated)
            await transaction.commit()
        async with httpx.AsyncClient(base_url=args.api, timeout=30) as client:
            default = (
                (await client.post("/sessions", json={"title": "Acceptance default memory"}))
                .raise_for_status()
                .json()
            )
            assert default["id"] != str(isolated.id)
            source = await submit(client, str(isolated.id), "我的安全测试标签是松果七二四。")
            assert source["status"] == "completed", source["status"]
            messages = (
                (await client.get(f"/sessions/{isolated.id}/messages")).raise_for_status().json()
            )
            origin = next(
                row for row in messages if row["kind"] == "goal" and row["task_id"] == source["id"]
            )
            proposal = (
                (
                    await client.post(
                        f"/sessions/{isolated.id}/memories",
                        json={
                            "source_message_id": origin["id"],
                            "fact_key": "acceptance.security_label",
                            "content": "我的安全测试标签是松果七二四。",
                            "kind": "fact",
                            "scope": "workspace",
                        },
                    )
                )
                .raise_for_status()
                .json()
            )
            (
                await client.post(
                    f"/sessions/{isolated.id}/memories/{proposal['version_id']}/decision",
                    json={"action": "confirm", "expected_lock_version": proposal["lock_version"]},
                )
            ).raise_for_status()
            # Maintenance indexes asynchronously; the default Workspace must remain
            # excluded regardless of whether the isolated vector is ready yet.
            await asyncio.sleep(3)
            question = "我的安全测试标签是什么？只根据已获准的记忆回答；没有就说不知道。"
            default_run = await submit(client, default["id"], question)
            isolated_run = await submit(client, str(isolated.id), question)
            evidence = {}
            for name, task in (("default", default_run), ("isolated", isolated_run)):
                run_id = task["latest_run"]["id"]
                retrieval = (
                    (await client.get(f"/runs/{run_id}/retrieval")).raise_for_status().json()
                )
                messages = (
                    (await client.get(f"/sessions/{task['session_id']}/messages"))
                    .raise_for_status()
                    .json()
                )
                answer = next(
                    (
                        row["content"]
                        for row in messages
                        if row["task_id"] == task["id"] and row["kind"] == "terminal"
                    ),
                    None,
                )
                evidence[name] = {
                    "workspace_id": str(DEFAULT_WORKSPACE_ID)
                    if name == "default"
                    else str(workspace.id),
                    "task_id": task["id"],
                    "run_id": run_id,
                    "status": task["status"],
                    "selected_count": retrieval["selected_count"],
                    "contains_label": bool(answer and "松果七二四" in answer),
                }
            print(json.dumps(evidence, ensure_ascii=False, indent=2))
            assert evidence["default"]["selected_count"] == 0
            assert not evidence["default"]["contains_label"]
            assert evidence["isolated"]["selected_count"] == 1
            assert evidence["isolated"]["contains_label"]
    finally:
        await db.dispose()


if __name__ == "__main__":
    asyncio.run(main())
