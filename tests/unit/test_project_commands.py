"""M3 项目命令契约的单元测试（实施计划 §8 X-02/X-03/X-04）。

覆盖验收要求：结构化 argv（拒绝 shell 拼接）、固定且受约束的 cwd、可执行文件白名单、
环境变量白名单、输出上限与超时、默认离线与独立网络授权。
"""

import asyncio
import os
import sys
from pathlib import Path
from uuid import uuid4

import pytest

from evoagent.projects.commands import (
    CommandSpec,
    build_environment,
    command_category,
    command_readable_roots,
    run_command,
    validate_argv,
    validate_cwd,
)
from evoagent.tools.base import ToolExecutionError, ToolPermissionError

PYTHON = Path(sys.executable).name
ALLOWLIST = (PYTHON, "pytest")


@pytest.mark.asyncio
@pytest.mark.skipif(sys.platform != "win32", reason="可信本机命令只在 Windows 上运行")
async def test_windows_host_command_requires_explicit_network_acknowledgement(
    tmp_path: Path,
) -> None:
    with pytest.raises(ToolPermissionError, match="allow_network=true"):
        await run_command(
            tmp_path,
            CommandSpec((sys.executable, "-V")),
            allowlist=ALLOWLIST,
            timeout_seconds=10,
            output_limit=1024,
            trusted_host_mode=True,
        )


@pytest.mark.asyncio
@pytest.mark.skipif(sys.platform != "win32", reason="可信本机命令只在 Windows 上运行")
async def test_windows_host_command_runs_only_when_explicitly_enabled(tmp_path: Path) -> None:
    spec = CommandSpec((sys.executable, "-V"), allow_network=True)
    with pytest.raises(ToolExecutionError, match="Linux Landlock/seccomp"):
        await run_command(
            tmp_path,
            spec,
            allowlist=ALLOWLIST,
            timeout_seconds=10,
            output_limit=1024,
        )
    outcome = await run_command(
        tmp_path,
        spec,
        allowlist=ALLOWLIST,
        timeout_seconds=10,
        output_limit=1024,
        trusted_host_mode=True,
    )
    assert outcome.return_code == 0
    assert "Python" in outcome.stdout + outcome.stderr


@pytest.mark.asyncio
@pytest.mark.skipif(sys.platform != "win32", reason="可信本机命令只在 Windows 上运行")
async def test_windows_host_command_timeout_terminates_process(tmp_path: Path) -> None:
    outcome = await run_command(
        tmp_path,
        CommandSpec(
            ("powershell", "-NoProfile", "-Command", "Start-Sleep -Seconds 5"),
            allow_network=True,
        ),
        allowlist=("powershell",),
        timeout_seconds=0.2,
        output_limit=1024,
        trusted_host_mode=True,
    )
    assert outcome.timed_out
    assert outcome.return_code != 0


def test_validate_argv_rejects_shell_metacharacters() -> None:
    with pytest.raises(ToolExecutionError, match="shell 元字符"):
        validate_argv((PYTHON, "-c", "print(1); print(2)"), allowlist=ALLOWLIST)
    with pytest.raises(ToolExecutionError, match="shell 元字符"):
        validate_argv((PYTHON, "-m", "pytest", "&&", "rm"), allowlist=ALLOWLIST)


def test_trusted_host_shell_still_requires_explicit_allowlist() -> None:
    argv = ("powershell", "-NoProfile", "-Command", "Get-Location; Get-ChildItem")
    with pytest.raises(ToolExecutionError, match="shell"):
        validate_argv(argv, allowlist=("powershell",))
    with pytest.raises(ToolExecutionError, match="允许列表"):
        validate_argv(argv, allowlist=(), trusted_host_mode=True)
    assert validate_argv(argv, allowlist=("powershell",), trusted_host_mode=True) == argv
    with pytest.raises(ToolExecutionError, match="shell 元字符"):
        validate_argv((PYTHON, "-c", "cat a | b"), allowlist=ALLOWLIST)
    with pytest.raises(ToolExecutionError, match="shell"):
        validate_argv(("cmd", "/c", "echo hi"), allowlist=("cmd",))


def test_validate_argv_rejects_shell_interpreters() -> None:
    for program in ("cmd.exe", "powershell", "pwsh", "bash", "sh"):
        with pytest.raises(ToolExecutionError, match="shell 解释器|不在允许列表"):
            validate_argv((program, "-c", "echo"), allowlist=(program,))


def test_command_category_is_derived_from_argv_and_publish_is_blocked() -> None:
    assert command_category(("pytest", "-q")) == "check"
    assert command_category(("python", "-m", "pytest")) == "check"
    assert command_category(("npm", "run", "build")) == "build"
    assert command_category(("python", "-m", "pip", "install", "x")) == "install"
    assert command_category(("python", "-c", "print(1)")) == "general"
    with pytest.raises(ToolPermissionError, match="发布与 Git 远端"):
        validate_argv(("git", "push"), allowlist=("git",))


def test_validate_argv_requires_allowlist_membership() -> None:
    with pytest.raises(ToolExecutionError, match="不在允许列表"):
        validate_argv(("curl", "https://example.com"), allowlist=ALLOWLIST)
    with pytest.raises(ToolExecutionError, match="未授权任何命令"):
        validate_argv((PYTHON, "-V"), allowlist=())
    assert validate_argv((PYTHON, "-V"), allowlist=ALLOWLIST) == (PYTHON, "-V")


def test_validate_argv_bounds_size() -> None:
    with pytest.raises(ToolExecutionError, match="参数过多"):
        validate_argv((PYTHON, *("x" for _ in range(70))), allowlist=ALLOWLIST)
    with pytest.raises(ToolExecutionError, match="过长"):
        validate_argv((PYTHON, "a" * 20_000), allowlist=ALLOWLIST)


def test_validate_cwd_stays_inside_project(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    (root / "tests").mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()

    physical, display = validate_cwd(root, "tests")
    assert physical == root / "tests"
    assert display == "tests"
    assert validate_cwd(root, ".")[1] == "."

    with pytest.raises(ToolPermissionError):
        validate_cwd(root, "../outside")
    with pytest.raises(ToolExecutionError, match="不存在"):
        validate_cwd(root, "missing")


def test_build_environment_excludes_secrets_and_proxies(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EVOAGENT_API_KEY", "secret")
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example:8080")
    monkeypatch.setenv("PATH", "/usr/bin")

    offline = build_environment(allow_network=False)
    online = build_environment(allow_network=True)

    assert "EVOAGENT_API_KEY" not in offline
    assert "HTTPS_PROXY" not in offline
    assert offline["PATH"] == "/usr/bin"
    assert offline["EVOAGENT_COMMAND_NETWORK"] == "isolated"
    assert online["EVOAGENT_COMMAND_NETWORK"] == "allowed"


def test_command_readable_roots_include_interpreter_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """解释器装在 `/usr` 之外时（CI 的 setup-python），放行清单必须覆盖它。

    GitHub Actions 把 Python 装在 `/opt/hostedtoolcache/Python/...`，而隔离子进程是
    先施加 Landlock、再 `os.execvpe` 目标解释器；清单里只有系统目录时 exec 会 EACCES，
    命令以 `EVOAGENT_COMMAND_SETUP_FAILED` 失败——这正是 CI 上项目命令全红的原因。
    """

    outside = Path("/opt/hostedtoolcache/Python/3.13.7/x64")
    monkeypatch.setattr(sys, "executable", str(outside / "bin" / "python3.13"))
    monkeypatch.setattr(sys, "prefix", str(outside))
    monkeypatch.setattr(sys, "base_prefix", str(outside))

    roots = command_readable_roots()

    assert Path("/usr") in roots
    assert Path("/etc/ssl") in roots
    assert outside.resolve() in roots
    assert (outside / "bin").resolve() in roots
    assert len(roots) == len(set(roots))


@pytest.mark.asyncio
@pytest.mark.skipif(sys.platform == "win32", reason="项目命令要求 Linux 内核隔离")
async def test_run_command_returns_structured_success(tmp_path: Path) -> None:
    outcome = await run_command(
        tmp_path,
        CommandSpec(argv=(PYTHON, "-c", "print('hello')")),
        allowlist=ALLOWLIST,
        timeout_seconds=30,
        output_limit=10_000,
    )

    assert outcome.return_code == 0
    assert "hello" in outcome.stdout
    assert outcome.timed_out is False
    assert outcome.stdout_truncated is False
    assert outcome.cwd == "."
    assert outcome.duration_seconds >= 0


@pytest.mark.asyncio
@pytest.mark.skipif(sys.platform == "win32", reason="项目命令要求 Linux 内核隔离")
async def test_run_command_reports_nonzero_exit_as_evidence(tmp_path: Path) -> None:
    program = "import sys\nsys.stderr.write('boom')\nsys.exit(3)"
    outcome = await run_command(
        tmp_path,
        CommandSpec(argv=(PYTHON, "-c", program)),
        allowlist=ALLOWLIST,
        timeout_seconds=30,
        output_limit=10_000,
    )

    assert outcome.return_code == 3
    assert "boom" in outcome.stderr
    assert outcome.timed_out is False


@pytest.mark.asyncio
@pytest.mark.skipif(sys.platform == "win32", reason="项目命令要求 Linux 内核隔离")
async def test_run_command_truncates_output(tmp_path: Path) -> None:
    outcome = await run_command(
        tmp_path,
        CommandSpec(argv=(PYTHON, "-c", "print('x' * 5000)")),
        allowlist=ALLOWLIST,
        timeout_seconds=30,
        output_limit=512,
    )

    assert outcome.stdout_truncated is True
    assert "已截断" in outcome.stdout


@pytest.mark.asyncio
@pytest.mark.skipif(sys.platform == "win32", reason="项目命令要求 Linux 内核隔离")
async def test_run_command_times_out_and_kills(tmp_path: Path) -> None:
    program = "import time\ntime.sleep(30)"
    outcome = await run_command(
        tmp_path,
        CommandSpec(argv=(PYTHON, "-c", program)),
        allowlist=ALLOWLIST,
        timeout_seconds=1,
        output_limit=1_000,
    )

    assert outcome.timed_out is True
    assert outcome.duration_seconds < 20


@pytest.mark.asyncio
@pytest.mark.skipif(sys.platform == "win32", reason="项目命令要求 Linux 内核隔离")
async def test_run_command_rejects_path_arguments_outside_root(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()

    with pytest.raises(ToolPermissionError, match="项目根之外"):
        await run_command(
            root,
            CommandSpec(argv=(PYTHON, "-c", "print(1)", "../outside.txt")),
            allowlist=ALLOWLIST,
            timeout_seconds=30,
            output_limit=1_000,
        )


@pytest.mark.asyncio
@pytest.mark.skipif(sys.platform == "win32", reason="项目命令要求 Linux 内核隔离")
async def test_run_command_uses_project_cwd(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    (root / "sub").mkdir(parents=True)
    (root / "sub" / "marker.txt").write_text("here", encoding="utf-8")

    outcome = await run_command(
        root,
        CommandSpec(
            argv=(PYTHON, "-c", "import pathlib\nprint(pathlib.Path('marker.txt').read_text())"),
            cwd="sub",
        ),
        allowlist=ALLOWLIST,
        timeout_seconds=30,
        output_limit=1_000,
    )

    assert outcome.cwd == "sub"
    assert "here" in outcome.stdout


def test_process_quota_must_leave_room_for_a_real_command() -> None:
    """进程数上限低于下限就拒绝启动：把上限调到 1 会让每条命令都必然失败。"""

    with pytest.raises(ToolExecutionError, match="进程数上限"):
        asyncio.run(
            run_command(
                Path("."),
                CommandSpec(argv=(PYTHON, "-c", "print(1)")),
                allowlist=ALLOWLIST,
                timeout_seconds=5,
                output_limit=1_000,
                max_processes=2,
            )
        )


@pytest.mark.asyncio
@pytest.mark.skipif(sys.platform == "win32", reason="项目命令要求 Linux 内核隔离")
async def test_run_command_kills_a_runaway_process_tree(tmp_path: Path) -> None:
    """失控的 fork 循环必须被整组杀掉，并如实上报 `process_limit_exceeded`。

    这里**不**依赖内核的 `RLIMIT_NPROC`——它的计数是全 user namespace 共享的（同机同
    UID 的别的容器也算），实测在上限 40 时连第一次 fork 都拒绝，做不了 per-command 配额。
    配额由父进程按会话实施，因此本用例同时验证"命令被提前终止"和"没有子进程活下来"。
    """

    token = f"evoagent-fork-probe-{uuid4().hex}"
    program = (
        "import os, time\n"
        f"marker = {token!r}\n"
        "for _ in range(200):\n"
        "    pid = os.fork()\n"
        "    if pid == 0:\n"
        "        time.sleep(60)\n"
        "        os._exit(0)\n"
        "time.sleep(60)\n"
        "print('done')\n"
    )
    outcome = await run_command(
        tmp_path,
        CommandSpec(argv=(PYTHON, "-c", program)),
        allowlist=ALLOWLIST,
        timeout_seconds=90,
        output_limit=10_000,
        max_processes=16,
    )

    assert outcome.process_limit_exceeded is True
    assert outcome.timed_out is False, "应当是进程树配额杀的，不是跑满超时"
    assert "done" not in outcome.stdout
    assert outcome.duration_seconds < 10, outcome.duration_seconds
    # 子进程是被 fork 出来的（没有 exec），因此它们的 cmdline 与命令相同：搜同一个 token
    # 就能发现存活者。整组被 SIGKILL，稍等片刻应当一个都不剩。
    for _ in range(20):
        if not _survivors(token):
            break
        await asyncio.sleep(0.1)
    assert _survivors(token) == [], "配额触发后仍有子进程活着"


@pytest.mark.asyncio
@pytest.mark.skipif(sys.platform == "win32", reason="项目命令要求 Linux 内核隔离")
async def test_run_command_allows_a_normal_multi_process_command(tmp_path: Path) -> None:
    """配额不能误伤正常命令：几个子进程并行干活应当照常跑完。"""

    program = (
        "import os, sys, time\n"
        "pids = [os.fork() for _ in range(4)]\n"
        "for pid in pids:\n"
        "    if pid == 0:\n"
        "        time.sleep(0.2)\n"
        "        os._exit(0)\n"
        "for pid in pids:\n"
        "    os.waitpid(pid, 0)\n"
        "print('children:4')\n"
    )
    outcome = await run_command(
        tmp_path,
        CommandSpec(argv=(PYTHON, "-c", program)),
        allowlist=ALLOWLIST,
        timeout_seconds=30,
        output_limit=1_000,
        max_processes=32,
    )

    assert outcome.return_code == 0, outcome.stderr
    assert "children:4" in outcome.stdout
    assert outcome.process_limit_exceeded is False


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


@pytest.mark.skipif(sys.platform == "win32", reason="信号与进程组是 Linux 语义")
def test_process_tree_watcher_kills_the_group_when_the_tree_grows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """父进程观察层：会话进程数超限时必须杀整组并如实标记（内核层失效时的兜底）。

    这一层用假进程对象验证逻辑本身，因为"内核不拦"只在 `CAP_SYS_RESOURCE`（容器以 root 跑）
    时出现，测试环境无法稳定构造那种身份。
    """

    import evoagent.projects.commands as module

    killed: list[tuple[int, int]] = []

    class FakeProcess:
        pid = 4321

    async def scenario() -> None:
        drain = asyncio.get_running_loop().create_future()
        watcher = asyncio.ensure_future(
            module._watch_process_tree(FakeProcess(), 8, drain)  # type: ignore[arg-type]
        )
        await asyncio.sleep(0)  # 让监视器进入第一轮等待
        monkeypatch.setattr(module, "_session_process_count", lambda session: 9)
        # Windows 上没有 `os.killpg`；这里只验证"超限时确实调用了它"。
        monkeypatch.setattr(
            module.os,
            "killpg",
            lambda pid, sig: killed.append((pid, sig)) or None,
            raising=False,
        )
        exceeded = await asyncio.wait_for(watcher, timeout=5)
        assert exceeded is True
        drain.cancel()

    asyncio.run(scenario())

    assert killed == [(4321, module.signal.SIGKILL)]
