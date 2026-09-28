"""Run inside the application Linux image to verify resource limits and the network switch.

配套 `scripts/check_project_command_isolation.py`（只查"离线时必须被阻断"）。这里补三件事：

1. `allow_network=True` 是**真的放行**（否则"离线被阻断"可能只是"网络永远不通"）；
2. 单进程内存上限生效（超过上限的分配必须失败，而不是把 Worker 拖死）；
3. 墙钟/CPU 上限生效（忙循环必须被终止，而不是永远跑下去）。

用法（在应用镜像里）：

    docker run --rm -v <repo>/scripts:/app/scripts:ro <image> \\
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
        print("resource limits and network switch verified")


if __name__ == "__main__":
    if not sys.platform.startswith("linux"):
        raise SystemExit("Linux container required")
    os.environ["EVOAGENT_API_KEY"] = "must-not-reach-command"
    asyncio.run(main())
