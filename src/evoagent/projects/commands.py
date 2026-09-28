"""在已授权项目内以结构化 argv 运行命令（实施计划 §8 X-01/X-02/X-03/X-04）。

契约：

- **结构化 argv**：不接受 shell 字符串。`shell=True` 与 `;`/`&&`/管道等元字符一律拒绝，
  因此不存在"拼接命令"这条路径。
- **固定 cwd**：工作目录只能是项目根内的目录，且解析后必须仍在根内。
- **可执行文件白名单**：只允许配置里显式列出的程序名（默认空 = 不能运行任何命令）。
- **环境变量白名单**：只传出最小集合（PATH/SystemRoot/TEMP/…），不继承应用秘密与代理设置。
- **输出上限与超时**：stdout/stderr 各自截断，超时后强杀并如实报告。
- **系统隔离**：命令子进程先安装 Landlock 文件视图、CPU/内存限制；
  默认再由 seccomp 阻断非 Unix socket，缺少内核能力时拒绝启动。
  `allow_network=true` 仍需独立授权（R2 审批）。
"""

from __future__ import annotations

import asyncio
import os
import re
import signal
import sys
import tempfile
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from evoagent.projects.schema import resolve_inside_root
from evoagent.tools.base import ToolExecutionError, ToolPermissionError

MAX_ARGV_ITEMS = 64
MAX_ARGV_BYTES = 16_384
MAX_CWD_LENGTH = 4_096

# 这些字符一旦出现就说明调用方想做 shell 拼接，而不是结构化 argv。
# 注意不把换行算进来：`python -c "<多行脚本>"` 是正常用法，而这里本来就不过 shell，
# 换行没有特殊含义（真正的注入通道是分号、管道、重定向与命令替换）。
SHELL_METACHARACTERS = (";", "&&", "||", "|", ">", "<", "`", "$(")
_SHELL_METACHARACTER_PATTERN = re.compile(r"[;&|<>`]|\$\(")

# 子进程需要的系统变量白名单；不含任何密钥、代理或应用配置。
ENV_ALLOWLIST = (
    "PATH",
    "PATHEXT",
    "SystemRoot",
    "SystemDrive",
    "COMSPEC",
    "WINDIR",
    "TEMP",
    "TMP",
    "TMPDIR",
    "HOME",
    "LANG",
    "LC_ALL",
    "PYTHONIOENCODING",
    "PYTHONUTF8",
    "PYTHONDONTWRITEBYTECODE",
)

# 只传入"名字"参数（如 `-m pytest`、`-c`）时的判断用不到，这里只处理路径形态的参数。
_PATH_LIKE = re.compile(r"^[.~/\\]|[/\\]")


@dataclass(frozen=True, slots=True)
class CommandSpec:
    """一次可审查的命令请求。"""

    argv: tuple[str, ...]
    cwd: str = "."
    allow_network: bool = False


@dataclass(frozen=True, slots=True)
class CommandOutcome:
    return_code: int | None
    stdout: str
    stderr: str
    stdout_truncated: bool
    stderr_truncated: bool
    duration_seconds: float
    timed_out: bool
    cwd: str
    program: str


def command_category(argv: Sequence[str]) -> str:
    """Classify exact, common read/check forms; unknown argv never inherits test approval."""

    if not argv:
        return "general"
    program = Path(argv[0]).name.lower().removesuffix(".exe")
    args = tuple(item.lower() for item in argv[1:])
    if program in {"pytest", "py.test", "mypy", "pyright"}:
        return "check"
    if program == "ruff" and args and args[0] == "check":
        return "check"
    if re.fullmatch(r"python(?:3(?:\.\d+)?)?", program) and len(args) >= 2 and args[0] == "-m":
        if args[1] in {"pytest", "unittest", "mypy"}:
            return "check"
        if args[1] in {"pip", "ensurepip"}:
            return "install"
    if program in {"pip", "pip3"}:
        return "install"
    if program in {"npm", "pnpm"} and args:
        if args[0] == "test" or args[:2] == ("run", "test"):
            return "check"
        if args[0] in {"install", "ci", "add", "update"}:
            return "install"
        if args[0] == "publish":
            return "publish"
        if args[:2] == ("run", "build"):
            return "build"
    if program == "git" and args:
        if args[0] in {"status", "diff", "log", "show"}:
            return "check"
        if args[0] in {"push", "remote"}:
            return "publish"
    return "general"


def validate_argv(argv: Sequence[str], *, allowlist: Sequence[str]) -> tuple[str, ...]:
    """校验 argv 的结构、长度与可执行文件白名单，返回规范化后的元组。"""

    if not argv:
        raise ToolExecutionError("argv 不能为空")
    if len(argv) > MAX_ARGV_ITEMS:
        raise ToolExecutionError(f"argv 参数过多（上限 {MAX_ARGV_ITEMS}）")
    encoded = 0
    for item in argv:
        if not isinstance(item, str) or item == "":
            raise ToolExecutionError("argv 的每一项都必须是非空字符串")
        if "\x00" in item:
            raise ToolExecutionError("argv 含非法字符")
        encoded += len(item.encode("utf-8"))
    if encoded > MAX_ARGV_BYTES:
        raise ToolExecutionError(f"argv 过长（上限 {MAX_ARGV_BYTES} 字节）")

    program = argv[0]
    if _SHELL_METACHARACTER_PATTERN.search(program):
        raise ToolExecutionError("第一个参数必须是可执行文件名，不能包含 shell 元字符")
    for item in argv[1:]:
        if any(token in item for token in SHELL_METACHARACTERS):
            raise ToolExecutionError("参数含 shell 元字符；请用结构化 argv，不要拼接命令")

    name = Path(program).name
    if command_category(argv) == "publish":
        raise ToolPermissionError("发布与 Git 远端操作需要独立工具授权，不能通过项目命令执行")
    if name.lower() in {"cmd", "cmd.exe", "powershell", "powershell.exe", "pwsh", "sh", "bash"}:
        raise ToolExecutionError("不允许通过 shell 解释器执行命令")
    if name not in allowlist:
        raise ToolExecutionError(
            f"可执行文件 {name} 不在允许列表内；当前允许："
            + (", ".join(sorted(allowlist)) if allowlist else "（空，未授权任何命令）")
        )
    return tuple(argv)


def command_readable_roots() -> tuple[Path, ...]:
    """命令子进程在 Landlock 下必须能**读并执行**的目录。

    除了系统目录，还必须包含**应用自己的解释器前缀**：CI（GitHub Actions 的
    `setup-python`）把 Python 装在 `/opt/hostedtoolcache/Python/...`，而隔离子进程是
    先施加 Landlock、再 `os.execvpe` 目标解释器；若白名单只有 `/usr` 等系统目录，
    exec 会 `EACCES`，命令以 `EVOAGENT_COMMAND_SETUP_FAILED`（退出码 125）失败——
    表现就是"所有项目命令测试在 CI 上全红"。

    放行解释器前缀不扩大攻击面：那是 Worker 自己正在执行的受信代码，
    而原白名单本来就放行了整个 `/usr`。
    """

    roots = [Path("/usr"), Path("/bin"), Path("/lib"), Path("/lib64"), Path("/etc/ssl")]
    interpreter = Path(sys.executable).resolve()
    roots.append(interpreter.parent)
    for prefix in (sys.prefix, getattr(sys, "base_prefix", sys.prefix)):
        if prefix:
            roots.append(Path(prefix).resolve())
    return tuple(dict.fromkeys(roots))


def validate_cwd(root: Path, cwd: str) -> tuple[Path, str]:
    """cwd 必须是项目根内的目录。"""

    if len(cwd) > MAX_CWD_LENGTH:
        raise ToolExecutionError("工作目录路径过长")
    _lexical, physical = resolve_inside_root(root, cwd)
    if not physical.exists():
        raise ToolExecutionError("工作目录不存在")
    if not physical.is_dir():
        raise ToolExecutionError("工作目录不是一个目录")
    try:
        display = physical.relative_to(root).as_posix() or "."
    except ValueError:  # pragma: no cover - resolve_inside_root 已保证在根内
        display = "."
    return physical, display


def validate_arguments_inside_root(root: Path, argv: Sequence[str]) -> None:
    """拒绝看起来像路径的参数指向根外。

    只处理"像路径"的参数（含分隔符或以 `.`/`~` 开头），普通选项与测试 ID 不参与判断；
    因此这是一道**额外**防线，不是路径解析本身。
    """

    for item in argv[1:]:
        if not item or item.startswith("-"):
            continue
        if not _PATH_LIKE.search(item):
            continue
        candidate = item.split("=", 1)[-1] if item.startswith("--") else item
        if not _PATH_LIKE.search(candidate):
            continue
        try:
            resolve_inside_root(root, candidate)
        except ToolPermissionError as error:
            raise ToolPermissionError(f"参数 {item!r} 指向项目根之外：{error}") from error
        except ToolExecutionError:
            # 目标不存在是正常的（例如引用即将生成的报告路径）；只要词法上没越界即可。
            continue


def build_environment(
    *,
    allow_network: bool,
    extra: Mapping[str, str] | None = None,
    base: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """构造最小环境；网络隔离由子进程的 seccomp 过滤器执行。

    `extra` 是部署者在配置里显式声明的变量（例如 `PYTHONPATH=src`）；模型无法指定环境变量，
    因此这里不存在"用环境变量改行为"的通道。
    """

    source = base if base is not None else os.environ
    environment = {name: source[name] for name in ENV_ALLOWLIST if name in source}
    environment.setdefault("PYTHONIOENCODING", "utf-8")
    environment.setdefault("PYTHONUTF8", "1")
    if extra:
        environment.update({str(key): str(value) for key, value in extra.items()})
    # 真实网络边界由 command_runner 安装；环境变量只供任务自检。
    environment["EVOAGENT_COMMAND_NETWORK"] = "allowed" if allow_network else "isolated"
    if not allow_network:
        # 清掉代理变量，避免"以为离线其实走了代理"。
        for name in (
            "HTTP_PROXY",
            "HTTPS_PROXY",
            "ALL_PROXY",
            "http_proxy",
            "https_proxy",
            "all_proxy",
        ):
            environment.pop(name, None)
    return environment


async def run_command(
    root: Path,
    spec: CommandSpec,
    *,
    allowlist: Sequence[str],
    timeout_seconds: float,
    output_limit: int,
    environment_extra: Mapping[str, str] | None = None,
    memory_limit_bytes: int = 1_073_741_824,
) -> CommandOutcome:
    """执行一次结构化命令并返回结构化结果；超时或越界都以异常或字段如实表达。"""

    argv = validate_argv(spec.argv, allowlist=allowlist)
    cwd, cwd_display = validate_cwd(root, spec.cwd)
    validate_arguments_inside_root(root, argv)

    if not sys.platform.startswith("linux"):
        raise ToolExecutionError("项目命令需要 Linux Landlock/seccomp 隔离执行环境")
    if memory_limit_bytes < 134_217_728:
        raise ToolExecutionError("项目命令内存上限不能低于 128 MiB")

    environment = build_environment(allow_network=spec.allow_network, extra=environment_extra)
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="evoagent-command-") as scratch:
        environment["TMPDIR"] = scratch
        environment["TEMP"] = scratch
        environment["TMP"] = scratch
        runner_argv = (
            sys.executable,
            "-m",
            "evoagent.projects.command_runner",
            str(root.resolve()),
            scratch,
            str(max(1, int(timeout_seconds) + 1)),
            str(memory_limit_bytes),
            "online" if spec.allow_network else "offline",
            "--",
            *argv,
        )
        try:
            process = await asyncio.create_subprocess_exec(
                *runner_argv,
                cwd=str(cwd),
                env=environment,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
            )
        except (OSError, ValueError) as error:
            raise ToolExecutionError(f"命令无法启动：{error}") from error
        timed_out = False
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout_seconds)
        except TimeoutError:
            timed_out = True
            os.killpg(process.pid, signal.SIGKILL)
            try:
                stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=5)
            except (TimeoutError, ProcessLookupError):  # pragma: no cover - 强杀兜底
                stdout, stderr = b"", b""
        if process.returncode == 125 and b"EVOAGENT_COMMAND_SETUP_FAILED:" in stderr:
            raise ToolExecutionError(stderr.decode("utf-8", errors="replace").strip())
    duration = time.perf_counter() - started

    text_out, out_truncated = _decode(stdout, output_limit)
    text_err, err_truncated = _decode(stderr, output_limit)
    return CommandOutcome(
        return_code=process.returncode,
        stdout=text_out,
        stderr=text_err,
        stdout_truncated=out_truncated,
        stderr_truncated=err_truncated,
        duration_seconds=round(duration, 3),
        timed_out=timed_out,
        cwd=cwd_display,
        program=Path(argv[0]).name,
    )


def _decode(raw: bytes, limit: int) -> tuple[str, bool]:
    truncated = len(raw) > limit
    payload = raw[:limit] if truncated else raw
    text = payload.decode("utf-8", errors="replace")
    if truncated:
        text += f"\n…（输出超过 {limit} 字节，已截断）"
    return text, truncated


__all__ = [
    "ENV_ALLOWLIST",
    "CommandOutcome",
    "CommandSpec",
    "command_category",
    "build_environment",
    "run_command",
    "validate_argv",
    "validate_cwd",
]
