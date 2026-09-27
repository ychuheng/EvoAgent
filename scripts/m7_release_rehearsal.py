"""M7 干净环境发布演练：对一个**已启动的**部署做端到端检查。

计划 §12 要求"在干净 Windows + Docker 环境做从零启动和升级演练"，并列出环境检查、
首次配置、项目挂载、数据库迁移、备份/恢复、日志与数据清理、网络/模型不可用降级。
本脚本负责其中**能从 HTTP 接口核实**的部分，并**明确拒绝**把没做的事写成通过：

- 健康检查、运行模式与 Worker 存活；
- 注册一个项目根（`--project-root`，只读档位）并核对 `root_status`；
- 建会话、建任务，等到终态；
- 核对 Trace（工具调用、账本状态）：出现 `unknown` 效果即判失败；
- 产出机器可读 JSON（`--output`），报告里引用它。

它**不做**：真实模型任务、备份/恢复、降级实验（那些在 M7 报告里按 docker 命令逐条记录），
也不会因为"接口通了"就声称 Agent 会做事——离线模式下模型是 `WorkerDemoProvider`。

用法：
    python scripts/m7_release_rehearsal.py --project-root <目录> [--base-url http://127.0.0.1:18010]
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
TERMINAL = {"completed", "failed", "cancelled"}


class RehearsalError(RuntimeError):
    """演练前置条件不成立（配置问题，不是产品缺陷）。"""


def check(name: str, passed: bool, detail: object = None) -> dict:
    return {"check": name, "passed": bool(passed), "detail": detail}


def wait_for_terminal(client: httpx.Client, task_id: str, *, timeout: float) -> dict:
    deadline = time.monotonic() + timeout
    task = client.get(f"/api/v1/tasks/{task_id}").raise_for_status().json()
    while task["status"] not in TERMINAL and time.monotonic() < deadline:
        time.sleep(1.0)
        task = client.get(f"/api/v1/tasks/{task_id}").raise_for_status().json()
    return task


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:18010")
    parser.add_argument(
        "--project-root",
        type=str,
        required=True,
        help=(
            "项目根，按 **API 进程看到的路径** 填写；compose 部署里是容器内绝对路径"
            "（例如 /app/evals/fixtures/beacon-holdout）"
        ),
    )
    parser.add_argument(
        "--local-check",
        action="store_true",
        help="额外用本机路径检查它是否是个目录（API 在容器里时不要开）",
    )
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--output", type=Path, default=ROOT / "output/m7-rehearsal.json")
    args = parser.parse_args()

    if args.local_check and not Path(args.project_root).is_dir():
        raise RehearsalError(f"项目根不存在：{args.project_root}")

    checks: list[dict] = []
    with httpx.Client(base_url=args.base_url, timeout=30.0) as client:
        ready = client.get("/health/ready")
        checks.append(check("api_ready", ready.status_code == 200, ready.json()))

        info = client.get("/api/v1/runtime-info").raise_for_status().json()
        checks.append(
            check(
                "runtime_info_shape",
                {"provider_mode", "search_mode", "worker_status"} <= set(info),
                info,
            )
        )
        checks.append(
            check("worker_ready", info.get("worker_status") == "ready", info.get("worker_status"))
        )
        checks.append(
            check(
                "runtime_info_hides_secrets",
                not any(key in json.dumps(info).lower() for key in ("api_key", "sk-", "base_url")),
                sorted(info),
            )
        )

        ui = client.get("/ui/")
        checks.append(
            check(
                "web_ui_served",
                ui.status_code == 200 and "<html" in ui.text.lower(),
                {"status": ui.status_code, "bytes": len(ui.text)},
            )
        )

        project = client.post(
            "/api/v1/projects",
            json={"path": args.project_root, "name": "m7-rehearsal", "authorization": "read"},
        )
        if project.status_code >= 400:
            raise RehearsalError(f"项目登记失败：{project.status_code} {project.text[:200]}")
        project_body = project.json()
        checks.append(
            check(
                "project_registered",
                project_body.get("root_status") == "available",
                project_body.get("root_status"),
            )
        )

        session = (
            client.post(
                "/api/v1/sessions",
                json={"title": "M7 演练", "project_id": project_body["id"]},
            )
            .raise_for_status()
            .json()
        )
        checks.append(check("session_created", bool(session.get("id")), session.get("id")))

        task = (
            client.post(
                "/api/v1/tasks",
                json={
                    "session_id": session["id"],
                    "goal": "列出这个项目的入口文件并总结数据流。",
                    "project_id": project_body["id"],
                },
            )
            .raise_for_status()
            .json()
        )

        finished = wait_for_terminal(client, task["id"], timeout=args.timeout)
        checks.append(
            check("task_reached_terminal", finished["status"] in TERMINAL, finished["status"])
        )
        run_id = (finished.get("latest_run") or {}).get("id")
        checks.append(check("task_has_run", bool(run_id), run_id))

        trace: dict = {}
        if run_id:
            trace = client.get(f"/api/v1/runs/{run_id}/trace").raise_for_status().json()
            tool_names = sorted({call["tool_name"] for call in trace.get("tool_calls", [])})
            effects = trace.get("tool_effects", [])
            unknown = [item["id"] for item in effects if item.get("status") == "unknown"]
            checks.append(check("trace_recorded_tools", bool(tool_names), tool_names))
            checks.append(check("no_unknown_effects", not unknown, unknown))
            checks.append(
                check(
                    "final_answer_present",
                    bool((trace.get("final_answer") or "").strip()),
                    (trace.get("final_answer") or "")[:120],
                )
            )

    report = {
        "schema_version": 1,
        "created_at": datetime.now(UTC).isoformat(),
        "base_url": args.base_url,
        "project_root": str(args.project_root),
        "mode": "offline_mock",
        "scope": (
            "从零启动后的接口级演练：健康、模式可见性、项目登记、任务终态与 Trace；"
            "离线 Provider 是 WorkerDemoProvider，不构成 Agent 能力或真实模型证据"
        ),
        "checks": checks,
        "harness_passed": all(item["passed"] for item in checks),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    for item in checks:
        print(f"[{'通过' if item['passed'] else '未通过'}] {item['check']}: {item['detail']}")
    print(f"演练结果：{'通过' if report['harness_passed'] else '未通过'}；输出 {args.output}")
    return 0 if report["harness_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
