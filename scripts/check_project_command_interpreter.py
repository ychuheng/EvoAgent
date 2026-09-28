"""验证"应用自己装在 `/usr` 之外"时项目命令仍能执行（CI 上的真实失败场景）。

GitHub Actions 的 `setup-python` 把解释器装在 `/opt/hostedtoolcache/Python/...`，
而 Landlock 的只读白名单最初只包含 `/usr`、`/bin`、`/lib`、`/lib64`、`/etc/ssl`。
隔离子进程在 `os.execvpe` 之前先施加 Landlock，于是**目标解释器本身不可读**：
命令以 `EVOAGENT_COMMAND_SETUP_FAILED` / 退出码 125 失败——所有项目命令测试在 CI 上全红。

做法：把当前解释器**复制**到 `/usr` 之外（不依赖 `ensurepip`，slim 镜像里常缺），
再用这份副本重跑本脚本，于是 `sys.executable` 落在 `/usr` 之外，正好复现 CI 的条件。

注意：命令子进程的环境变量走白名单，默认**不继承 `PYTHONPATH`**。若在旧镜像里直接挂载
源码运行，父进程导入的是挂载源码、子进程导入的却是镜像里已安装的旧 `evoagent`，
于是校验的是旧代码（实测就是因此误判过一次）。因此这里显式把 `PYTHONPATH` 作为
`environment_extra` 传给命令子进程，保证父子导入同一份源码。

用法（在 Linux 容器里）：
    python scripts/check_project_command_interpreter.py
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from evoagent.projects.commands import CommandSpec, run_command

DEFAULT_OUTSIDE = Path("/app/workspace/evoagent-outside-python")
MARKER = "outside-prefix:ok"


async def verify() -> None:
    python = Path(sys.executable).resolve()
    if str(python).startswith("/usr"):
        raise SystemExit(f"这条检查必须在 /usr 之外的解释器下运行（当前 {python}）")
    print(f"interpreter={python} prefix={sys.prefix}")
    with tempfile.TemporaryDirectory(dir="/app/workspace") as directory:
        root = Path(directory)
        pythonpath = os.environ.get("PYTHONPATH")
        outcome = await run_command(
            root,
            CommandSpec(argv=(str(python), "-c", f"print('{MARKER}')")),
            allowlist=(python.name,),
            timeout_seconds=30,
            output_limit=10_000,
            environment_extra={"PYTHONPATH": pythonpath} if pythonpath else None,
        )
        if outcome.return_code != 0 or MARKER not in outcome.stdout:
            raise SystemExit(
                f"解释器在 /usr 之外时命令无法执行：rc={outcome.return_code} "
                f"stdout={outcome.stdout[:200]!r} stderr={outcome.stderr[:200]!r}"
            )
        print("command with an interpreter outside /usr: allowed")


def main() -> int:
    if not sys.platform.startswith("linux"):
        raise SystemExit("Linux container required")
    outside_dir = Path(os.environ.get("EVOAGENT_OUTSIDE_PYTHON", DEFAULT_OUTSIDE))
    if str(Path(sys.executable).resolve()).startswith("/usr"):
        outside_dir.mkdir(parents=True, exist_ok=True)
        copy = outside_dir / Path(sys.executable).name
        shutil.copy2(Path(sys.executable).resolve(), copy)
        copy.chmod(0o755)
        environment = {**os.environ, "EVOAGENT_OUTSIDE_PYTHON": str(outside_dir)}
        return subprocess.run([str(copy), os.path.abspath(__file__)], env=environment).returncode
    asyncio.run(verify())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
