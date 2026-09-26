"""Run the frozen synthetic user-task set against a dedicated local deployment."""

import argparse
import asyncio
import hashlib
import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic

import httpx


async def wait_for_task(
    client: httpx.AsyncClient, task_id: str, *, approval_response: str | None
) -> dict:
    deadline = monotonic() + 120
    approved: set[str] = set()
    while monotonic() < deadline:
        task = (await client.get(f"/tasks/{task_id}")).raise_for_status().json()
        if task["status"] in {"completed", "failed", "cancelled"}:
            return task
        if task["status"] == "waiting_user" and approval_response is not None:
            trace = (
                (await client.get(f"/runs/{task['latest_run']['id']}/trace"))
                .raise_for_status()
                .json()
            )
            calls = {call["id"]: call for call in trace["tool_calls"]}
            for approval in trace["approvals"]:
                if approval["status"] != "pending" or approval["id"] in approved:
                    continue
                if calls.get(approval["tool_call_id"], {}).get("tool_name") != "ask_user":
                    raise RuntimeError("refusing to auto-approve a non-ask_user tool")
                (
                    await client.post(
                        f"/tool-approvals/{approval['id']}/approve",
                        json={"response": approval_response},
                    )
                ).raise_for_status()
                approved.add(approval["id"])
        await asyncio.sleep(1)
    await client.post(f"/tasks/{task_id}/cancel")
    raise TimeoutError(f"task did not reach a terminal state: {task_id}")


async def run_case(client: httpx.AsyncClient, case: dict) -> dict:
    session = (
        (await client.post("/sessions", json={"title": f"Agent quality {case['id']}"}))
        .raise_for_status()
        .json()
    )
    steps = []
    for step in case["steps"]:
        task = (
            (
                await client.post(
                    "/tasks",
                    json={
                        "session_id": session["id"],
                        "goal": step["goal"],
                        "acceptance": step.get("acceptance"),
                    },
                )
            )
            .raise_for_status()
            .json()
        )
        final = await wait_for_task(
            client, task["id"], approval_response=step.get("approval_response")
        )
        trace = (
            (await client.get(f"/runs/{final['latest_run']['id']}/trace")).raise_for_status().json()
        )
        tools = Counter(
            call["tool_name"] for call in trace["tool_calls"] if call["status"] == "succeeded"
        )
        checked = [
            event for event in trace["events"] if event["event_type"] == "acceptance.checked"
        ]
        requirements = step.get("minimum_tool_calls", {})
        passed = (
            final["status"] == "completed"
            and (not step.get("acceptance") or bool(checked and checked[-1]["payload"]["passed"]))
            and all(tools.get(name, 0) >= count for name, count in requirements.items())
            and len(trace["artifacts"]) >= step.get("minimum_artifacts", 0)
        )
        steps.append(
            {
                "task_id": task["id"],
                "run_id": trace["run_id"],
                "status": final["status"],
                "error_code": trace["error_code"],
                "tool_successes": dict(tools),
                "artifact_count": len(trace["artifacts"]),
                "acceptance_passed": checked[-1]["payload"]["passed"] if checked else None,
                "passed": passed,
            }
        )
        if not passed:
            break
    return {
        "case_id": case["id"],
        "category": case["category"],
        "session_id": session["id"],
        "passed": len(steps) == len(case["steps"]) and all(step["passed"] for step in steps),
        "steps": steps,
    }


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", required=True, help="dedicated loopback /api/v1 endpoint")
    parser.add_argument(
        "--dataset", type=Path, default=Path("evals/datasets/agent-core-user-v1.json")
    )
    parser.add_argument("--output", type=Path, default=Path("output/agent-core-user-v1.json"))
    args = parser.parse_args()
    if not args.api.startswith("http://127.0.0.1:") or not args.api.endswith("/api/v1"):
        parser.error("--api must be a loopback HTTP /api/v1 address")
    raw_dataset = args.dataset.read_bytes()
    dataset = json.loads(raw_dataset)
    if dataset["schema_version"] != 1 or len(dataset["cases"]) < 10:
        parser.error("unexpected dataset schema or size")

    results = []
    async with httpx.AsyncClient(base_url=args.api, timeout=30) as client:
        runtime = (await client.get("/runtime-info")).raise_for_status().json()
        if runtime["provider_mode"] != "real":
            parser.error("quality evaluation requires a real model deployment")
        for case in dataset["cases"]:
            try:
                result = await run_case(client, case)
            except (httpx.HTTPError, TimeoutError, RuntimeError, KeyError) as error:
                result = {
                    "case_id": case["id"],
                    "category": case["category"],
                    "passed": False,
                    "error_type": type(error).__name__,
                }
            results.append(result)
            print(f"{case['id']}: {'PASS' if result['passed'] else 'FAIL'}", flush=True)

    passed = sum(result["passed"] for result in results)
    report = {
        "dataset": dataset["name"],
        "dataset_sha256": hashlib.sha256(raw_dataset).hexdigest(),
        "run_at": datetime.now(UTC).isoformat(),
        "provider": runtime["provider"],
        "model": runtime["model"],
        "search_mode": runtime["search_mode"],
        "case_count": len(results),
        "passed": passed,
        "pass_rate": passed / len(results),
        "minimum_pass_rate": dataset["minimum_pass_rate"],
        "gate_passed": passed / len(results) >= dataset["minimum_pass_rate"],
        "cases": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {key: report[key] for key in ("passed", "case_count", "pass_rate", "gate_passed")},
            ensure_ascii=False,
        )
    )
    return 0 if report["gate_passed"] else 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
