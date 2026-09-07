"""Run 文件空间和受控子进程的实际能力边界。"""

import asyncio
import os
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

from evoagent.tools.base import ToolExecutionError, ToolPermissionError


class RunSandbox:
    """把每个 Run 的写入限制在独立目录。"""

    def __init__(self, root: Path, run_id: UUID) -> None:
        self.root = (root.expanduser().resolve(strict=False) / str(run_id)).resolve(strict=False)
        self.root.mkdir(parents=True, exist_ok=True)

    async def write_text(self, path: str, content: str, *, overwrite: bool) -> Path:
        return await asyncio.to_thread(self._write_text, path, content, overwrite)

    def _write_text(self, path: str, content: str, overwrite: bool) -> Path:
        target = (self.root / path).resolve(strict=False)
        if not target.is_relative_to(self.root):
            raise ToolPermissionError("file write outside the run sandbox is not allowed")
        if target.exists() and not overwrite:
            raise ToolExecutionError("target file already exists")
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.parent / f".{target.name}.{uuid4().hex}.tmp"
        temporary.write_text(content, encoding="utf-8")
        os.replace(temporary, target)
        return target


@dataclass(frozen=True, slots=True)
class ShellResult:
    return_code: int
    stdout: str
    stderr: str


class ShellSandbox:
    """只运行 allowlist 中的 argv，不启用 Shell 解释器。"""

    def __init__(
        self,
        workspace: Path,
        *,
        allowed_executables: tuple[str, ...],
        timeout_seconds: float,
        max_output_chars: int = 20_000,
    ) -> None:
        self._workspace = workspace.resolve(strict=False)
        self._allowed = frozenset(allowed_executables)
        self._timeout = timeout_seconds
        self._max_output_chars = max_output_chars

    async def run(self, argv: tuple[str, ...]) -> ShellResult:
        if not argv or argv[0] not in self._allowed:
            raise ToolPermissionError("executable is not in the shell allowlist")
        environment = {
            key: value
            for key, value in os.environ.items()
            if key.upper() in {"PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "TEMP", "TMP"}
        }
        process = await asyncio.create_subprocess_exec(
            *argv,
            cwd=self._workspace,
            env=environment,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=self._timeout)
        except TimeoutError as error:
            process.kill()
            await process.wait()
            raise ToolExecutionError("shell command timed out") from error
        return ShellResult(
            return_code=process.returncode or 0,
            stdout=stdout.decode("utf-8", errors="replace")[: self._max_output_chars],
            stderr=stderr.decode("utf-8", errors="replace")[: self._max_output_chars],
        )
