"""在**当前环境**核对项目命令的可观测性预检（K2 / §13.4）。

与 `personal_preflight.py` 的分工：那个脚本校验的是配置文件，本脚本只回答一件事——
**这个环境能不能观测会话进程数**。两者共用同一个 `ProjectCommandReadinessProbe`，
但结论不互为证明：宿主上通过不代表容器里能观测。

因此它的主要用途是在**实际 Worker 的运行环境**里跑：

    docker run --rm -v "<repo>:/app" -w /app python:3.12-slim \\
        sh -c "pip install -q pydantic; PYTHONPATH=/app/src \\
               python /app/scripts/check_command_readiness.py"

退出码：0 = 就绪或明确不需要命令执行（`disabled` / `trusted_host`）；
2 = 不可观测（`project_command_unobservable`），此时 Worker 不会注册项目命令工具。
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from evoagent.projects.readiness import (  # noqa: E402
    REASON_UNOBSERVABLE,
    ProjectCommandReadinessProbe,
)


def main() -> int:
    try:
        from evoagent.config import Settings

        settings = Settings()
    except Exception as error:  # noqa: BLE001 - 缺依赖时退化成只读所需字段
        print(f"settings unavailable ({error.__class__.__name__}); probing with defaults")
        settings = SimpleNamespace(project_command_allowlist=("git",), trusted_host_mode=False)

    result = ProjectCommandReadinessProbe().check(settings)
    print(f"command readiness: {result.reason} — {result.detail}")
    if result.session_id is not None:
        print(f"session = {result.session_id}, counted processes = {result.session_process_count}")
    if result.reason == REASON_UNOBSERVABLE:
        print("不可观测：Worker 不会注册项目命令工具", file=sys.stderr)
        return 2
    print("ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
