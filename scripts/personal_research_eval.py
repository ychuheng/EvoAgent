"""Freeze and evaluate public research tasks without treating URL matching as fact checking."""

import argparse
import asyncio
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic

import httpx

from evoagent.evals.citations import report_from_trace

REQUIRED_CHECKS = {
    "min_searches",
    "min_read_urls",
    "min_fetched_answer_links",
    "max_unobserved_answer_links",
}


def load_dataset(path: Path) -> tuple[dict, str]:
    raw = path.read_bytes()
    dataset = json.loads(raw)
    if dataset.get("schema_version") != 1 or not dataset.get("cases"):
        raise ValueError("expected a nonempty research dataset with schema_version 1")
    seen: set[str] = set()
    for case in dataset["cases"]:
        case_id = case.get("id")
        checks = case.get("checks")
        if not isinstance(case_id, str) or not case_id or case_id in seen:
            raise ValueError("research case IDs must be unique nonempty strings")
        seen.add(case_id)
        if not isinstance(case.get("goal"), str) or not case["goal"].strip():
            raise ValueError(f"{case_id}: goal is required")
        if not isinstance(checks, dict) or set(checks) != REQUIRED_CHECKS:
            raise ValueError(f"{case_id}: invalid automatic checks")
        if any(type(value) is not int or value < 0 for value in checks.values()):
            raise ValueError(f"{case_id}: check limits must be nonnegative integers")
        if not case.get("review_questions") or not all(
            isinstance(item, str) and item.strip() for item in case["review_questions"]
        ):
            raise ValueError(f"{case_id}: human review questions are required")
    return dataset, hashlib.sha256(raw).hexdigest()


def score_trace(case: dict, task: dict, trace: dict) -> dict:
    sources = trace.get("sources") or {}
    searches = sources.get("searches") or []
    reads = sources.get("reads") or []
    links = sources.get("answer_links") or []
    read_urls = {item["final_url"] for item in reads if item.get("final_url")}
    fetched = [item for item in links if item.get("level") == "fetched_text"]
    unobserved = [item for item in links if item.get("level") == "unobserved"]
    limits = case["checks"]
    # 逐结论引用核对：上面的检查只说明"引用过来源"，这里说明"每条结论引用的来源是否真读过正文"。
    citations = report_from_trace(trace)
    checks = {
        "task_completed": task.get("status") == "completed",
        "real_search_observed": bool(searches)
        and all(item.get("provider") in {"brave", "ddgs"} for item in searches),
        "enough_searches": len(searches) >= limits["min_searches"],
        "enough_read_urls": len(read_urls) >= limits["min_read_urls"],
        "enough_fetched_answer_links": len(fetched) >= limits["min_fetched_answer_links"],
        "no_extra_unobserved_links": len(unobserved) <= limits["max_unobserved_answer_links"],
        "conclusions_backed_by_read_text": citations.passed,
    }
    return {
        "automatic_checks": checks,
        "automatic_passed": all(checks.values()),
        "search_count": len(searches),
        "read_urls": sorted(read_urls),
        "answer_links": [{"url": item.get("url"), "level": item.get("level")} for item in links],
        "per_conclusion": {
            "counts": citations.counts,
            "passed": citations.passed,
            "failures": [
                {"index": item.index, "text": item.text, "verdict": item.verdict}
                for item in citations.failures
            ],
        },
        "human_review": {
            "status": "pending",
            "questions": case["review_questions"],
            "note": citations.note,
        },
    }


async def wait_for_terminal(client: httpx.AsyncClient, task_id: str) -> dict:
    deadline = monotonic() + 180
    while monotonic() < deadline:
        task = (await client.get(f"/tasks/{task_id}")).raise_for_status().json()
        if task["status"] in {"completed", "failed", "cancelled"}:
            return task
        if task["status"] == "waiting_user":
            return task
        await asyncio.sleep(1)
    await client.post(f"/tasks/{task_id}/cancel")
    raise TimeoutError(f"research task timed out: {task_id}")


async def run_case(client: httpx.AsyncClient, case: dict) -> dict:
    session = (
        (await client.post("/sessions", json={"title": f"Research eval {case['id']}"}))
        .raise_for_status()
        .json()
    )
    created = (
        (await client.post("/tasks", json={"session_id": session["id"], "goal": case["goal"]}))
        .raise_for_status()
        .json()
    )
    task = await wait_for_terminal(client, created["id"])
    run_id = task["latest_run"]["id"]
    trace = (await client.get(f"/runs/{run_id}/trace")).raise_for_status().json()
    return {
        "case_id": case["id"],
        "category": case["category"],
        "session_id": session["id"],
        "task_id": task["id"],
        "run_id": run_id,
        "status": task["status"],
        "error_code": trace.get("error_code"),
        **score_trace(case, task, trace),
    }


async def execute(args: argparse.Namespace, dataset: dict, digest: str) -> int:
    if (
        not args.api
        or not args.api.startswith("http://127.0.0.1:")
        or not args.api.endswith("/api/v1")
    ):
        raise ValueError("--api must be a loopback HTTP /api/v1 address")
    async with httpx.AsyncClient(base_url=args.api, timeout=30) as client:
        runtime = (await client.get("/runtime-info")).raise_for_status().json()
        if runtime.get("provider_mode") != "real" or runtime.get("search_mode") not in {
            "brave",
            "ddgs",
        }:
            raise ValueError("research evaluation requires a real model and non-Mock search")
        results = []
        for case in dataset["cases"]:
            try:
                result = await run_case(client, case)
            except (httpx.HTTPError, TimeoutError, KeyError, ValueError) as error:
                result = {
                    "case_id": case["id"],
                    "category": case["category"],
                    "automatic_passed": False,
                    "human_review": {"status": "pending", "questions": case["review_questions"]},
                    "error_type": type(error).__name__,
                }
            results.append(result)
            print(f"{case['id']}: {'PASS' if result['automatic_passed'] else 'FAIL'}")
    report = {
        "dataset": dataset["name"],
        "dataset_sha256": digest,
        "run_at": datetime.now(UTC).isoformat(),
        "model": runtime.get("model"),
        "provider": runtime.get("provider"),
        "search_mode": runtime["search_mode"],
        "automatic_passed": sum(item["automatic_passed"] for item in results),
        "case_count": len(results),
        "human_review_status": "pending",
        "human_scoring": dataset["human_scoring"],
        "cases": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", "utf-8")
    print(f"Report: {args.output}")
    return 0 if all(item["automatic_passed"] for item in results) else 2


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dataset", type=Path, default=Path("evals/datasets/personal-research-v1.json")
    )
    parser.add_argument("--run", action="store_true", help="execute paid live tasks")
    parser.add_argument("--api", help="dedicated loopback /api/v1 endpoint")
    parser.add_argument("--output", type=Path, default=Path("output/personal-research-v1.json"))
    args = parser.parse_args()
    try:
        dataset, digest = load_dataset(args.dataset)
        print(f"Dataset: {dataset['name']} ({len(dataset['cases'])} cases), SHA-256 {digest}")
        if not args.run:
            return 0
        return asyncio.run(execute(args, dataset, digest))
    except (ValueError, json.JSONDecodeError) as error:
        parser.error(str(error))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
