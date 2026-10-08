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
import subprocess
import sys
import tempfile
import time
from collections.abc import Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

from evoagent.projects.schema import resolve_inside_root
from evoagent.tools.base import ToolExecutionError, ToolPermissionError

MAX_ARGV_ITEMS = 64
MAX_ARGV_BYTES = 16_384
MAX_CWD_LENGTH = 4_096

# 一条命令最多能同时存在多少进程（含它自己）。这是**进程树**配额：失控的 fork 循环
# 无法通过 3 → 6 → 12 的方式炸掉 Worker。默认值给正常构建/测试留足余量
# （`make -j`、`pytest -n auto` 通常只用到几十个进程）。
DEFAULT_MAX_PROCESSES = 256
MIN_PROCESSES = 8
# 父进程盯进程树的轮询间隔；命令是低频操作，0.05 秒足够便宜，同时把"有界超杀"压到很小。
_PROCESS_POLL_SECONDS = 0.05

# 这些字符一旦出现就说明调用方想做 shell 拼接，而不是结构化 argv。
# 注意不把换行算进来：`python -c "<多行脚本>"` 是正常用法，而这里本来就不过 shell，
# 换行没有特殊含义（真正的注入通道是分号、管道、重定向与命令替换）。
SHELL_METACHARACTERS = (";", "&&", "||", "|", ">", "<", "`", "$(")
_SHELL_METACHARACTER_PATTERN = re.compile(r"[;&|<>`]|\$\(")


class CommandUnobservableError(ToolExecutionError):
    """无法观测这条会话的进程树（K2 / §13.4）。

    进程数配额靠 `/proc` 计数实施；看不见就不能假装健康——发起前与运行中都是如此。
    这**不**声称补齐了内核 pids/cgroup 硬配额或后代会话逃逸的完整验证。
    """

    code = "project_command_unobservable"


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
    process_limit_exceeded: bool = False


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


def validate_argv(
    argv: Sequence[str], *, allowlist: Sequence[str], trusted_host_mode: bool = False
) -> tuple[str, ...]:
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
        if not trusted_host_mode and any(token in item for token in SHELL_METACHARACTERS):
            raise ToolExecutionError("参数含 shell 元字符；请用结构化 argv，不要拼接命令")

    name = Path(program).name
    if command_category(argv) == "publish":
        raise ToolPermissionError("发布与 Git 远端操作需要独立工具授权，不能通过项目命令执行")
    if not trusted_host_mode and name.lower() in {
        "cmd",
        "cmd.exe",
        "powershell",
        "powershell.exe",
        "pwsh",
        "sh",
        "bash",
    }:
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
    max_processes: int = DEFAULT_MAX_PROCESSES,
    trusted_host_mode: bool = False,
) -> CommandOutcome:
    """执行一次结构化命令并返回结构化结果；超时或越界都以异常或字段如实表达。

    进程数上限是**父进程按会话实施的进程树配额**（见 `_watch_process_tree`）：超限即
    杀掉整条命令的进程组，`process_limit_exceeded` 如实置位。这里**没有**用内核的
    `RLIMIT_NPROC`，因为它的计数是全 user namespace 共享的（同机同 UID 的其它容器
    也算在内），做不了 per-command 配额；原委记在 `_watch_process_tree` 的文档里。
    """

    argv = validate_argv(spec.argv, allowlist=allowlist, trusted_host_mode=trusted_host_mode)
    cwd, cwd_display = validate_cwd(root, spec.cwd)
    # A trusted host interpreter may include paths in its command text. Approval,
    # not lexical path inspection, is the boundary for that deliberately broad call.
    host_interpreter = Path(argv[0]).name.lower() in {
        "cmd",
        "cmd.exe",
        "powershell",
        "powershell.exe",
        "pwsh",
        "pwsh.exe",
    }
    if not (trusted_host_mode and host_interpreter):
        validate_arguments_inside_root(root, argv)

    if memory_limit_bytes < 134_217_728:
        raise ToolExecutionError("项目命令内存上限不能低于 128 MiB")
    if max_processes < MIN_PROCESSES:
        raise ToolExecutionError(f"项目命令进程数上限不能低于 {MIN_PROCESSES}")
    if trusted_host_mode:
        if os.name != "nt":
            raise ToolExecutionError("可信本机模式只支持 Windows 主机进程")
        return await _run_trusted_windows_command(
            argv,
            cwd=cwd,
            cwd_display=cwd_display,
            spec=spec,
            timeout_seconds=timeout_seconds,
            output_limit=output_limit,
            environment_extra=environment_extra,
        )
    if not sys.platform.startswith("linux"):
        raise ToolExecutionError("项目命令需要 Linux Landlock/seccomp 隔离执行环境")

    # 发起前检查可观测性（K2 / §13.4）：启动时通过一次不能替代运行中的复查，
    # 但发起前必须先确认这条路现在真的能看见进程树。
    ensure_observable_session(os.getpid())

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
        process_limit_exceeded = False
        watcher_reason: str | None = None
        drain = asyncio.ensure_future(process.communicate())
        watcher = asyncio.ensure_future(_watch_process_tree(process, max_processes, drain))
        try:
            stdout, stderr = await asyncio.wait_for(asyncio.shield(drain), timeout=timeout_seconds)
        except TimeoutError:
            timed_out = True
            os.killpg(process.pid, signal.SIGKILL)
            try:
                stdout, stderr = await asyncio.wait_for(drain, timeout=5)
            except (TimeoutError, ProcessLookupError):  # pragma: no cover - 强杀兜底
                stdout, stderr = b"", b""
        finally:
            if watcher.done() and not watcher.cancelled():
                watcher_reason = watcher.result()
            watcher.cancel()
            with suppress(asyncio.CancelledError):
                await watcher
        process_limit_exceeded = watcher_reason == "process_limit"
        if watcher_reason == "unobservable":
            # 监控期间失去可观测性：进程组已被终止，这里如实报不可观测，
            # 不把"看不见"当成"没超限"。
            raise CommandUnobservableError("运行期间失去进程树可观测性，已终止该命令的进程组")
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
        process_limit_exceeded=process_limit_exceeded,
    )


async def _read_bounded(stream: asyncio.StreamReader, limit: int) -> tuple[bytes, bool]:
    chunks = bytearray()
    truncated = False
    while chunk := await stream.read(65_536):
        remaining = max(0, limit + 1 - len(chunks))
        chunks.extend(chunk[:remaining])
        if len(chunks) > limit or len(chunk) > remaining:
            truncated = True
    return bytes(chunks[:limit]), truncated


async def _kill_windows_tree(pid: int) -> None:
    try:
        killer = await asyncio.create_subprocess_exec(
            "taskkill",
            "/PID",
            str(pid),
            "/T",
            "/F",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(killer.wait(), timeout=5)
    except (OSError, TimeoutError) as error:
        raise ToolExecutionError(f"无法终止 Windows 命令进程树 {pid}: {error}") from error


async def _run_trusted_windows_command(
    argv: tuple[str, ...],
    *,
    cwd: Path,
    cwd_display: str,
    spec: CommandSpec,
    timeout_seconds: float,
    output_limit: int,
    environment_extra: Mapping[str, str] | None,
) -> CommandOutcome:
    """Explicitly approved Windows command, with no claim of OS sandboxing.

    An interpreter can reach outside the project using its own code. The UI must
    therefore approve every call and must describe this as trusted host access.
    """
    if not spec.allow_network:
        raise ToolPermissionError(
            "Windows 本机命令无法保证断网；请显式设置 allow_network=true 并等待逐次审批"
        )
    environment = build_environment(allow_network=True, extra=environment_extra)
    started = time.perf_counter()
    try:
        process = await asyncio.create_subprocess_exec(
            *argv,
            cwd=str(cwd),
            env=environment,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
        )
    except (OSError, ValueError) as error:
        raise ToolExecutionError(f"Windows 命令无法启动：{error}") from error
    assert process.stdout is not None and process.stderr is not None
    stdout_task = asyncio.create_task(_read_bounded(process.stdout, output_limit))
    stderr_task = asyncio.create_task(_read_bounded(process.stderr, output_limit))
    timed_out = False
    try:
        await asyncio.wait_for(process.wait(), timeout=timeout_seconds)
    except TimeoutError:
        timed_out = True
        await _kill_windows_tree(process.pid)
        await asyncio.wait_for(process.wait(), timeout=5)
    except asyncio.CancelledError:
        await _kill_windows_tree(process.pid)
        await asyncio.wait_for(process.wait(), timeout=5)
        stdout_task.cancel()
        stderr_task.cancel()
        raise
    try:
        (stdout, stdout_truncated), (stderr, stderr_truncated) = await asyncio.wait_for(
            asyncio.gather(stdout_task, stderr_task), timeout=5
        )
    except TimeoutError:
        stdout_task.cancel()
        stderr_task.cancel()
        raise ToolExecutionError("Windows 命令的子进程仍持有输出流，无法安全收尾") from None
    duration = time.perf_counter() - started
    return CommandOutcome(
        return_code=process.returncode,
        stdout=stdout.decode("utf-8", errors="replace"),
        stderr=stderr.decode("utf-8", errors="replace"),
        stdout_truncated=stdout_truncated,
        stderr_truncated=stderr_truncated,
        duration_seconds=round(duration, 3),
        timed_out=timed_out,
        cwd=cwd_display,
        program=Path(argv[0]).name,
    )


async def _watch_process_tree(
    process: asyncio.subprocess.Process, max_processes: int, drain: asyncio.Future
) -> str | None:
    """盯住这条会话的进程数；返回终止原因或 `None`（正常结束）。

    - `process_limit`：超限，整组被 SIGKILL；
    - `unobservable`：**监控期间失去可观测性**（`/proc` 读不了/解析不了）。这时也杀掉
      所控进程组并如实返回原因——不能因为"看不见"就当没超限（K2 / §13.4）。

    为什么不用内核的 `RLIMIT_NPROC`（试过，撤了）：它的计数是**整个 user namespace 里
    该 UID 的进程数**，不是某棵进程树，也不是某个容器的。本机实测同一个应用镜像里
    PID 命名空间内只有 1 个进程时，`RLIMIT_NPROC=40` 连**第一次** fork 都拒绝（EAGAIN），
    要到 80 才放行——差额来自同机其它同样以该 UID 运行的容器。这种"按 UID 共享"的语义
    既做不了per-command配额，还会让命令连自己的沙箱初始化（`ldconfig`）都跑不起来。

    因此配额完全由父进程按**会话**计数实施：命令子进程是 `setsid` 后的会话首进程，
    未被显式 `setsid` 的后代都留在同一会话里，double-fork 也甩不掉统计。代价是**有界
    超杀**：最多多跑一个轮询间隔的进程，随后整组被 SIGKILL。轮询间隔取 0.05 秒，
    命令是低频操作，这个开销可以忽略。
    """

    while True:
        try:
            count = _session_process_count(process.pid)
        except CommandUnobservableError:
            _kill_session(process)
            return "unobservable"
        if count > max_processes:
            _kill_session(process)
            return "process_limit"
        if drain.done():
            return None
        await asyncio.sleep(_PROCESS_POLL_SECONDS)


def _kill_session(process: asyncio.subprocess.Process) -> None:
    with suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGKILL)


def ensure_observable_session(pid: int) -> int:
    """核对 `/proc` 现在真的可观测，返回该进程的会话号；不可观测即抛错。

    三件事一起查（K2 / §13.4）：`self/stat` 能读且关键字段能解析、`/proc` 能枚举、
    会话计数至少包含自己。任何一项不成立都说明"进程树配额"这条路当前不成立——
    返回 0 或跳过都会让配额形同虚设。
    """

    try:
        with open(f"/proc/{pid}/stat", "rb") as handle:
            raw = handle.read()
    except OSError as error:
        raise CommandUnobservableError(f"无法读取 /proc/{pid}/stat：{error}") from error
    try:
        # `pid (comm) state ppid pgrp session …`：comm 可能含空格与括号，因此按**最后**
        # 一个 `") "` 切开。注意切开后的第一段是 state、不是 pid——pid 在前缀里，
        # 拿它跟请求的 pid 对一下，才能证明读到的确实是这个进程的记录。
        head, separator, tail = raw.rpartition(b") ")
        if not separator:
            raise ValueError("missing command terminator")
        stat_pid = int(head.split(b" ", 1)[0])
        fields = tail.split()
        session = int(fields[3])
    except (IndexError, ValueError) as error:
        raise CommandUnobservableError(f"/proc/{pid}/stat 的关键字段无法解析") from error
    if stat_pid != pid:
        raise CommandUnobservableError(f"/proc/{pid}/stat 报告的 pid 是 {stat_pid}，与请求不一致")
    if _session_process_count(session) < 1:
        raise CommandUnobservableError("会话计数为 0：/proc 枚举结果不可信")
    return session


def _session_process_count(session_id: int) -> int:
    """统计 `/proc` 里会话号等于 `session_id` 的进程数。

    要么给出数字，要么抛"不可观测"——**不用 0 假装健康**（K2 / §13.4）：

    - 恰好在读取瞬间退出的条目允许跳过（`FileNotFoundError`）；
    - `/proc` 目录不可读、权限拒绝，或**仍存活**条目的关键字段读不到/解析不了，
      一律抛 `CommandUnobservableError`，不得静默忽略。
    """

    try:
        entries = os.listdir("/proc")
    except OSError as error:
        raise CommandUnobservableError(f"无法枚举 /proc：{error}") from error
    total = 0
    for entry in entries:
        if not entry.isdigit():
            continue
        try:
            with open(f"/proc/{entry}/stat", "rb") as handle:
                raw = handle.read()
        except FileNotFoundError:
            # 条目在 listdir 与 open 之间退出：这是允许跳过的竞态。
            continue
        except OSError as error:
            raise CommandUnobservableError(f"无法读取 /proc/{entry}/stat：{error}") from error
        try:
            # 格式：`pid (comm) state ppid pgrp session …`；comm 可能含空格与括号，
            # 因此按最后一个 `) ` 切开，之后的第 4 个字段才是 session（从 0 数起）。
            fields = raw.rsplit(b") ", 1)[1].split()
            session = int(fields[3])
        except (IndexError, ValueError) as error:
            # 条目还在（不是 FileNotFoundError）却解析不了：不得当作"没有这个进程"。
            raise CommandUnobservableError(f"/proc/{entry}/stat 的关键字段无法解析") from error
        if session == session_id:
            total += 1
    return total


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
