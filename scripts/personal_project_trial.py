"""Run one development-only real-model project task and save a compact audit record."""

import argparse
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic

import httpx


async def execute(args: argparse.Namespace) -> int:
    if not args.api.startswith("http://127.0.0.1:") or not args.api.endswith("/api/v1"):
        raise ValueError("--api must be a loopback /api/v1 URL")
    async with httpx.AsyncClient(base_url=args.api, timeout=30) as client:
        runtime = (await client.get("/runtime-info")).raise_for_status().json()
        budget = (await client.get("/budget-status")).raise_for_status().json()
        if runtime.get("provider_mode") != "real":
            raise ValueError("trial requires a real model")
        if budget.get("scope") != "trial" or not budget.get("allowed"):
            raise ValueError(f"trial budget is closed: {budget.get('reason')}")
        project = (
            (
                await client.post(
                    "/projects",
                    json={
                        "path": args.project_root,
                        "name": "dev project trial",
                        "authorization": args.authorization,
                    },
                )
            )
            .raise_for_status()
            .json()
        )
        if project["authorization"] != args.authorization:
            project = (
                (
                    await client.put(
                        f"/projects/{project['id']}/authorization",
                        json={"authorization": args.authorization},
                    )
                )
                .raise_for_status()
                .json()
            )
            project = (
                (await client.post(f"/projects/{project['id']}/check")).raise_for_status().json()
            )
        if project.get("root_status") != "available":
            raise ValueError(f"project root unavailable: {project.get('root_status')}")
        session = (
            (
                await client.post(
                    "/sessions",
                    json={"title": "dev trial", "project_id": project["id"]},
                )
            )
            .raise_for_status()
            .json()
        )
        task = (
            (
                await client.post(
                    "/tasks",
                    json={
                        "session_id": session["id"],
                        "goal": args.goal,
                        "input_paths": args.input_path,
                    },
                )
            )
            .raise_for_status()
            .json()
        )
        deadline = monotonic() + args.timeout
        intervention = None
        while task["status"] not in {"completed", "failed", "cancelled", "waiting_user"}:
            if monotonic() >= deadline:
                await client.post(f"/tasks/{task['id']}/cancel")
                raise TimeoutError(f"task exceeded {args.timeout} seconds")
            if args.intervention and intervention is None and task["status"] == "running":
                partial = (
                    (await client.get(f"/runs/{task['latest_run']['id']}/trace"))
                    .raise_for_status()
                    .json()
                )
                if len(partial.get("tool_calls", [])) >= args.intervention_after_tools:
                    response = await client.post(
                        f"/tasks/{task['id']}/instructions",
                        json={"content": args.intervention},
                    )
                    if response.status_code == 409:
                        intervention = {"sent": False, "reason": "task_ended_before_injection"}
                    else:
                        intervention = {
                            "sent": True,
                            "record": response.raise_for_status().json(),
                        }
            await asyncio.sleep(0.25 if args.intervention else 2)
            task = (await client.get(f"/tasks/{task['id']}")).raise_for_status().json()
        trace = (
            (await client.get(f"/runs/{task['latest_run']['id']}/trace")).raise_for_status().json()
        )
        budget_after = (await client.get("/budget-status")).raise_for_status().json()
    report = {
        "kind": "dev_trial_not_holdout",
        "recorded_at": datetime.now(UTC).isoformat(),
        "task_id": task["id"],
        "run_id": task["latest_run"]["id"],
        "status": task["status"],
        "error_code": trace.get("error_code"),
        "model": runtime.get("model"),
        "project_root": args.project_root,
        "input_paths": args.input_path,
        "tool_calls": [
            {"tool": call["tool_name"], "status": call["status"], "error": call.get("error_code")}
            for call in trace.get("tool_calls", [])
        ],
        "answer": trace.get("final_answer"),
        "intervention": intervention,
        "intervention_events": [
            {"sequence": event["sequence"], "type": event["event_type"]}
            for event in trace.get("events", [])
            if event["event_type"].startswith("instruction.")
        ],
        "pending_approvals": [
            approval["id"]
            for approval in trace.get("approvals", [])
            if approval["status"] == "pending"
        ],
        "budget_spent_micros": budget_after.get("spent_micros"),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"{task['status']}: {task['id']} (report: {args.output})")
    return 0 if task["status"] == "completed" else 2


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api", required=True)
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--authorization", choices=("read", "read_write"), default="read")
    parser.add_argument("--goal", required=True)
    parser.add_argument("--input-path", action="append", default=[])
    parser.add_argument("--intervention", help="Development-only instruction sent while running")
    parser.add_argument("--intervention-after-tools", type=int, default=1)
    parser.add_argument("--output", type=Path, default=Path("output/personal-project-trial.json"))
    parser.add_argument("--timeout", type=int, default=300)
    args = parser.parse_args()
    return asyncio.run(execute(args))


if __name__ == "__main__":
    raise SystemExit(main())
