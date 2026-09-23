"""验证构建后 API/UI/Worker/共享 Artifact 与关闭新能力的完整链路。"""

import asyncio
import json
from pathlib import Path

import httpx


async def main():
    async with httpx.AsyncClient(base_url="http://127.0.0.1:18000", timeout=10) as client:
        ready = await client.get("/health/ready")
        ready.raise_for_status()
        ui = await client.get("/ui/")
        ui.raise_for_status()
        assert '<script type="module"' in ui.text
        scope = await client.post("/api/v1/sessions", json={"title": "final deployment probe"})
        scope.raise_for_status()
        sid = scope.json()["id"]
        response = await client.post(
            "/api/v1/tasks", json={"session_id": sid, "goal": "离线搜索并生成 Markdown 报告"}
        )
        response.raise_for_status()
        task = response.json()
        async with asyncio.timeout(60):
            while True:
                current = await client.get(f"/api/v1/tasks/{task['id']}")
                current.raise_for_status()
                if current.json()["status"] in {"completed", "failed", "waiting_user"}:
                    break
                await asyncio.sleep(0.2)
        assert current.json()["status"] == "completed"
        run = task["latest_run"]["id"]
        trace = await client.get(f"/api/v1/runs/{run}/trace")
        trace.raise_for_status()
        context = await client.get(f"/api/v1/runs/{run}/context")
        context.raise_for_status()
        assert context.json()["policy"] == {}
        servers = await client.get("/api/v1/mcp/servers")
        assert servers.json() == []
        memory = await client.get(f"/api/v1/sessions/{sid}/memories")
        assert memory.json() == []
        assert any(effect["status"] == "committed" for effect in trace.json()["tool_effects"])
        result = {
            "ready": ready.json(),
            "ui_status": ui.status_code,
            "task_status": "completed",
            "run_id": run,
            "artifact_count": len(trace.json()["artifacts"]),
            "committed_effects": sum(
                e["status"] == "committed" for e in trace.json()["tool_effects"]
            ),
            "tools": [c["tool_name"] for c in trace.json()["tool_calls"]],
            "context_policy": "legacy",
            "memory_entries": 0,
            "mcp_servers": 0,
            "provider": "mock",
            "port": 18000,
            "passed": True,
        }
        Path("docs/reports/phase4-final-deployment.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print("deployment probe passed")


if __name__ == "__main__":
    asyncio.run(main())
