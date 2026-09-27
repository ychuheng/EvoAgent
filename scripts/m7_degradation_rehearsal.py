"""M7 降级演练：Redis 不可用、付费闸门拒付、模型服务不可达，以及恢复。

计划 §12 要求演练"网络/模型不可用降级"。本脚本在**已启动的 compose 部署**上按顺序做四件事，
每件都记录真实观测结果（不是期望值）：

1. `redis_down`：停掉 Redis，看新任务是否仍能被 Worker 认领（设计上 Redis 只是唤醒提示，
   事实来源是 PostgreSQL，Worker 有轮询兜底）；
2. `budget_refused`：换一个 `openai_compatible` 但**没填额度**的 Worker，任务应被预算闸门
   拒绝（`budget_exceeded`），而不是去联网；
3. `model_unreachable`：给同一 Worker 填上额度、把 Base URL 指向不可达地址，任务应以
   网络类错误码失败，而不是挂住或崩掉；
4. `recovery`：移除降级 Worker、恢复原 Worker，再次建任务应正常完成。

只做观测与编排，**不修改产品代码**；结束时一定把原 Worker 恢复（`finally`）。

用法：
    python scripts/m7_degradation_rehearsal.py --compose-project evoagent-m7 \\
        --compose-file docker-compose.yml --compose-file tmp/m7-override.yml
"""

from __future__ import annotations

import argparse
import json
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
TERMINAL = {"completed", "failed", "cancelled"}
DEGRADED_WORKER = "evoagent-degraded-worker"
BUSY_PROVIDER = "http://127.0.0.1:9"


def docker(*arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["docker", *arguments], capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    if check and result.returncode != 0:
        raise RuntimeError(f"docker {' '.join(arguments)} 失败：{result.stderr.strip()[:200]}")
    return result


class Deployment:
    """用 compose 命令与容器内省驱动一次部署。"""

    def __init__(self, project: str, files: list[str], base_url: str) -> None:
        self.project = project
        self.files = files
        self.base_url = base_url

    def compose(self, *arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        command = ["compose", "-p", self.project]
        for item in self.files:
            command += ["-f", item]
        return docker(*command, *arguments, check=check)

    def container_id(self, service: str) -> str:
        result = self.compose("ps", "-q", service)
        return result.stdout.strip().splitlines()[0] if result.stdout.strip() else ""

    def inspect(self, container: str, template: str) -> str:
        return docker("inspect", "-f", template, container).stdout.strip()

    def worker_env(self) -> list[str]:
        """复制 compose Worker 的关键环境，再按场景覆盖。"""

        return [
            "-e",
            "EVOAGENT_DATABASE_URL=postgresql+asyncpg://evoagent:evoagent@postgres:5432/evoagent",
            "-e",
            "EVOAGENT_REDIS_URL=redis://redis:6379/0",
            "-e",
            "EVOAGENT_REDIS_NAMESPACE=evoagent-compose",
            "-e",
            "EVOAGENT_WORKSPACE=/app/workspace",
            "-e",
            "EVOAGENT_ARTIFACT_ROOT=/app/workspace/artifacts",
        ]

    def start_degraded_worker(self, extra_env: list[str]) -> None:
        api = self.container_id("api")
        if not api:
            raise RuntimeError("找不到 api 容器；降级演练需要已启动的部署")
        image = self.inspect(api, "{{.Config.Image}}")
        network = self.inspect(
            api,
            "{{range $name, $value := .NetworkSettings.Networks}}{{$name}}{{end}}",
        )
        docker("rm", "-f", DEGRADED_WORKER, check=False)
        docker(
            "run",
            "-d",
            "--name",
            DEGRADED_WORKER,
            "--network",
            network,
            "--volumes-from",
            api,
            *self.worker_env(),
            # 真实 Provider 必须显式给出模型上下文窗口，否则进程启动就失败（这是产品行为）。
            "-e",
            "EVOAGENT_CONTEXT_WINDOW_TOKENS=32768",
            *extra_env,
            image,
            "evoagent-worker",
        )
        time.sleep(5)
        state = self.inspect(DEGRADED_WORKER, "{{.State.Status}}")
        if state != "running":
            logs = docker("logs", "--tail", "20", DEGRADED_WORKER, check=False)
            raise RuntimeError(
                f"降级 Worker 未运行（{state}）：{(logs.stdout + logs.stderr).strip()[:400]}"
            )

    def stop_degraded_worker(self) -> None:
        docker("rm", "-f", DEGRADED_WORKER, check=False)


def create_task(
    client: httpx.Client, goal: str, *, provider: str | None = None, model: str | None = None
) -> tuple[str, str]:
    session = (
        client.post("/api/v1/sessions", json={"title": "M7 降级演练"}).raise_for_status().json()
    )
    payload: dict = {"session_id": session["id"], "goal": goal}
    if provider is not None:
        payload["provider"] = provider
    if model is not None:
        payload["model"] = model
    task = client.post("/api/v1/tasks", json=payload).raise_for_status().json()
    return task["id"], session["id"]


def wait_terminal(client: httpx.Client, task_id: str, *, timeout: float) -> dict:
    deadline = time.monotonic() + timeout
    task = client.get(f"/api/v1/tasks/{task_id}").raise_for_status().json()
    while task["status"] not in TERMINAL and time.monotonic() < deadline:
        time.sleep(1.0)
        task = client.get(f"/api/v1/tasks/{task_id}").raise_for_status().json()
    return task


def observe(client: httpx.Client, observed: dict, *, timeout: float) -> dict:
    """建一条任务并记录终态与错误码。"""

    task_id, _ = create_task(
        client,
        observed["goal"],
        provider=observed.get("provider"),
        model=observed.get("model"),
    )
    result = wait_and_observe(client, task_id, observed["scenario"], timeout=timeout)
    return result


def wait_and_observe(client: httpx.Client, task_id: str, scenario: str, *, timeout: float) -> dict:
    task = wait_terminal(client, task_id, timeout=timeout)
    run_id = (task.get("latest_run") or {}).get("id")
    result = {
        "scenario": scenario,
        "task_id": task_id,
        "run_id": run_id,
        "status": task["status"],
        "timed_out": task["status"] not in TERMINAL,
    }
    if run_id:
        trace = client.get(f"/api/v1/runs/{run_id}/trace").raise_for_status().json()
        result["run_status"] = trace.get("status")
        result["error_code"] = trace.get("error_code")
        result["tool_calls"] = sorted({call["tool_name"] for call in trace.get("tool_calls", [])})
        result["final_answer"] = (trace.get("final_answer") or "")[:120]
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compose-project", default="evoagent-m7")
    parser.add_argument("--compose-file", action="append", default=None)
    parser.add_argument("--base-url", default="http://127.0.0.1:18010")
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--output", type=Path, default=ROOT / "output/m7-degradation.json")
    args = parser.parse_args()
    files = args.compose_file or ["docker-compose.yml"]

    deployment = Deployment(args.compose_project, files, args.base_url)
    results: list[dict] = []
    notes: list[str] = []

    with httpx.Client(base_url=args.base_url, timeout=30.0) as client:
        try:
            # 1) Redis 不可用：设计上只是少一条唤醒提示，事实来源还是数据库；
            #    实际观测到任务进入 retrying/rate_limited，Redis 恢复后应能继续。
            deployment.compose("stop", "redis")
            ready = client.get("/health/ready")
            notes.append(f"redis 停止后 /health/ready={ready.status_code}")
            redis_task_id, _ = create_task(client, "Redis 停掉时这条任务还能被认领吗？")
            results.append(
                wait_and_observe(client, redis_task_id, "redis_down", timeout=args.timeout)
            )
            deployment.compose("start", "redis")
            time.sleep(3)
            results.append(
                wait_and_observe(
                    client, redis_task_id, "redis_down_recovered", timeout=args.timeout
                )
            )

            # 2) 付费闸门：真实 Provider + 未填额度 → 应直接拒绝，不联网。
            deployment.compose("stop", "worker")
            deployment.start_degraded_worker(
                [
                    "-e",
                    "EVOAGENT_PROVIDER=openai_compatible",
                    "-e",
                    "EVOAGENT_API_KEY=not-a-real-key",
                    "-e",
                    f"EVOAGENT_BASE_URL={BUSY_PROVIDER}",
                    "-e",
                    "EVOAGENT_MODEL=missing-model",
                    "-e",
                    "EVOAGENT_WORKER_ID=worker-degraded",
                ]
            )
            time.sleep(5)
            results.append(
                observe(
                    client,
                    {
                        "scenario": "budget_refused",
                        "goal": "未填额度时的付费模型调用应被预算闸门拒绝。",
                        # 任务里显式声明真实 Provider/模型：否则 Worker 会因「任务冻结配置与
                        # 自身不一致」直接判 provider_configuration_mismatch，走不到预算闸门。
                        "provider": "openai_compatible",
                        "model": "missing-model",
                    },
                    timeout=args.timeout,
                )
            )

            # 3) 模型不可达：填上额度与价格假设，Base URL 指向不可达地址。
            deployment.stop_degraded_worker()
            deployment.start_degraded_worker(
                [
                    "-e",
                    "EVOAGENT_PROVIDER=openai_compatible",
                    "-e",
                    "EVOAGENT_API_KEY=not-a-real-key",
                    "-e",
                    f"EVOAGENT_BASE_URL={BUSY_PROVIDER}",
                    "-e",
                    "EVOAGENT_MODEL=missing-model",
                    "-e",
                    "EVOAGENT_WORKER_ID=worker-degraded",
                    "-e",
                    "EVOAGENT_BUDGET_SCOPE=trial",
                    "-e",
                    "EVOAGENT_BUDGET_TRIAL_LIMIT_MICROS=1000000",
                    "-e",
                    "EVOAGENT_BUDGET_TASK_LIMIT_MICROS=1000000",
                    "-e",
                    "EVOAGENT_BUDGET_INPUT_PRICE_MICROS_PER_MILLION=1000",
                    "-e",
                    "EVOAGENT_BUDGET_OUTPUT_PRICE_MICROS_PER_MILLION=1000",
                    "-e",
                    "EVOAGENT_BUDGET_STOP_RATIO=1.0",
                ]
            )
            time.sleep(5)
            results.append(
                observe(
                    client,
                    {
                        "scenario": "model_unreachable",
                        "goal": "模型服务不可达时应以网络错误收场。",
                        "provider": "openai_compatible",
                        "model": "missing-model",
                    },
                    timeout=args.timeout,
                )
            )
        finally:
            # 4) 恢复：无论前面发生什么，都移除降级 Worker 并恢复原 Worker。
            deployment.stop_degraded_worker()
            deployment.compose("start", "worker")
            time.sleep(5)
            results.append(
                observe(
                    client,
                    {"scenario": "recovery", "goal": "恢复原 Worker 后应重新正常完成。"},
                    timeout=args.timeout,
                )
            )

    report = {
        "schema_version": 1,
        "created_at": datetime.now(UTC).isoformat(),
        "base_url": args.base_url,
        "compose_project": args.compose_project,
        "notes": notes,
        "scenarios": results,
        "observed_failures": {item["scenario"]: item.get("error_code") for item in results},
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    for item in results:
        print(
            f"[{item['scenario']}] status={item['status']} "
            f"run={item.get('run_status')} error={item.get('error_code')} "
            f"tools={item.get('tool_calls')}"
        )
    for note in notes:
        print(f"[信息] {note}")
    print(f"输出 {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
