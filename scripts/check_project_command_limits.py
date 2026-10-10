"""Run inside the application Linux image to verify resource limits and the network switch.

配套 `scripts/check_project_command_isolation.py`（只查"离线时必须被阻断"）。这里补五件事：

1. `allow_network=True` 是**真的放行**（否则"离线被阻断"可能只是"网络永远不通"）；
2. 单进程内存上限生效（超过上限的分配必须失败，而不是把 Worker 拖死）；
3. 墙钟/CPU 上限生效（忙循环必须被终止，而不是永远跑下去）；
4. 进程树配额生效：失控的 fork 循环**不能**跑完，且不能留下活着的子进程；
5. 配额内的普通子进程仍可运行；这是会话轮询软限制，不是 RLIMIT_NPROC/cgroup 硬上限。

用法（在应用镜像里）：

    docker run --rm -v <repo>/scripts:/app/scripts:ro -v <repo>/src:/app/src:ro \\
        -e PYTHONPATH=/app/src <image> \\
        python /app/scripts/check_project_command_limits.py

注意 `python -c` 的脚本里不能用 `;`：结构化 argv 会拒绝 shell 元字符（见
`projects/commands.py` 的 `SHELL_METACHARACTERS`），所以这里用换行分隔语句。
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

from evoagent.projects.commands import CommandSpec, run_command

MEGABYTE = 1024 * 1024
PROCESS_CAP = 16


def _survivors(token: str) -> list[int]:
    """当前 `/proc` 里命令行含 `token` 的进程号（用于确认整组真的死了）。"""

    found: list[int] = []
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        try:
            with open(f"/proc/{entry}/cmdline", "rb") as handle:
                if token.encode() in handle.read():
                    found.append(int(entry))
        except OSError:
            continue
    return found


async def main() -> None:
    if not sys.platform.startswith("linux"):
        raise SystemExit("Linux container required")
    python = Path(sys.executable).name
    with tempfile.TemporaryDirectory(dir="/app/workspace") as directory:
        root = Path(directory)

        # 1) 显式 allow_network=True：允许建立网络套接字。
        opened = await run_command(
            root,
            CommandSpec(
                argv=(
                    python,
                    "-c",
                    "import socket\nsocket.socket(socket.AF_INET)\nprint('network:OPEN')",
                ),
                allow_network=True,
            ),
            allowlist=(python,),
            timeout_seconds=15,
            output_limit=10_000,
        )
        assert opened.return_code == 0, opened.stderr
        assert "network:OPEN" in opened.stdout, opened.stdout
        print("allow_network=true: socket allowed (switch is real)")

        # 2) 内存上限：2 GiB 分配 vs 1 GiB 上限。
        memory = await run_command(
            root,
            CommandSpec(
                argv=(
                    python,
                    "-c",
                    "b = bytearray(2 * 1024 * 1024 * 1024)\nprint('allocated')",
                ),
            ),
            allowlist=(python,),
            timeout_seconds=30,
            output_limit=10_000,
            memory_limit_bytes=1 * 1024 * MEGABYTE,
        )
        assert "allocated" not in memory.stdout, memory.stdout
        assert memory.return_code != 0, memory.return_code
        print(f"memory_limit: 2GiB allocation refused (rc={memory.return_code})")

        # 3) 超时：忙循环必须被终止。
        busy = await run_command(
            root,
            CommandSpec(argv=(python, "-c", "while True: pass")),
            allowlist=(python,),
            timeout_seconds=3,
            output_limit=10_000,
        )
        assert busy.timed_out or busy.return_code != 0, busy
        print(f"timeout: busy loop stopped (timed_out={busy.timed_out})")

        # 4) 进程树配额：失控的 fork 循环必须被整组杀掉，且不留存活子进程。
        token = f"evoagent-fork-probe-{os.getpid()}"
        forking = (
            f"# {token}\n"
            "import os, time\n"
            "for _ in range(200):\n"
            "    pid = os.fork()\n"
            "    if pid == 0:\n"
            "        time.sleep(60)\n"
            "        os._exit(0)\n"
            "time.sleep(60)\n"
            "print('done')\n"
        )
        forking_result = await run_command(
            root,
            CommandSpec(argv=(python, "-c", forking)),
            allowlist=(python,),
            timeout_seconds=90,
            output_limit=10_000,
            max_processes=PROCESS_CAP,
        )
        assert forking_result.process_limit_exceeded is True, forking_result
        assert forking_result.timed_out is False, "应当是配额杀的，不是跑满超时"
        assert "done" not in forking_result.stdout
        assert forking_result.duration_seconds < 15, forking_result.duration_seconds
        for _ in range(20):
            if not _survivors(token):
                break
            await asyncio.sleep(0.1)
        assert _survivors(token) == [], "配额触发后仍有子进程活着"
        print(
            f"process quota: runaway tree killed in {forking_result.duration_seconds}s "
            f"(rc={forking_result.return_code}, no survivors)"
        )

        # 5) 正常的多进程命令不能被误伤。
        normal = await run_command(
            root,
            CommandSpec(
                argv=(
                    python,
                    "-c",
                    "import os, time\n"
                    "pids = []\n"
                    "for _ in range(4):\n"
                    "    pid = os.fork()\n"
                    "    if pid == 0:\n"
                    "        time.sleep(0.2)\n"
                    "        os._exit(0)\n"
                    "    pids.append(pid)\n"
                    "for pid in pids:\n"
                    "    os.waitpid(pid, 0)\n"
                    "print('children:4')\n",
                )
            ),
            allowlist=(python,),
            timeout_seconds=30,
            output_limit=10_000,
            max_processes=PROCESS_CAP,
        )
        assert normal.return_code == 0, normal.stderr
        assert "children:4" in normal.stdout, normal.stdout
        assert normal.process_limit_exceeded is False
        print("process quota: a normal 4-process command still runs")

        # 6) 紧配额下离线网络仍然被阻断：沙箱初始化不能依赖 fork
        #    （回归：`ctypes.util.find_library` 会 fork `ldconfig`，受限时静默失败）。
        blocked = await run_command(
            root,
            CommandSpec(
                argv=(
                    python,
                    "-c",
                    "import socket, sys\n"
                    "try:\n"
                    "    socket.socket(socket.AF_INET)\n"
                    "except OSError as error:\n"
                    "    print(f'network:BLOCKED:{error.errno}')\n"
                    "    sys.exit(0)\n"
                    "print('network:OPEN')\n"
                    "sys.exit(3)",
                )
            ),
            allowlist=(python,),
            timeout_seconds=15,
            output_limit=10_000,
            max_processes=PROCESS_CAP,
        )
        assert "network:BLOCKED" in blocked.stdout, blocked.stdout
        print("process quota: offline network still blocked")
        print("resource limits and network switch verified")


if __name__ == "__main__":
    if not sys.platform.startswith("linux"):
        raise SystemExit("Linux container required")
    os.environ["EVOAGENT_API_KEY"] = "must-not-reach-command"
    asyncio.run(main())
