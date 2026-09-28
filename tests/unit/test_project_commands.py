"""M3 项目命令契约的单元测试（实施计划 §8 X-02/X-03/X-04）。

覆盖验收要求：结构化 argv（拒绝 shell 拼接）、固定且受约束的 cwd、可执行文件白名单、
环境变量白名单、输出上限与超时、默认离线与独立网络授权。
"""

import sys
from pathlib import Path

import pytest

from evoagent.projects.commands import (
    CommandSpec,
    build_environment,
    command_category,
    run_command,
    validate_argv,
    validate_cwd,
)
from evoagent.tools.base import ToolExecutionError, ToolPermissionError

PYTHON = Path(sys.executable).name
ALLOWLIST = (PYTHON, "pytest")


def test_validate_argv_rejects_shell_metacharacters() -> None:
    with pytest.raises(ToolExecutionError, match="shell 元字符"):
        validate_argv((PYTHON, "-c", "print(1); print(2)"), allowlist=ALLOWLIST)
    with pytest.raises(ToolExecutionError, match="shell 元字符"):
        validate_argv((PYTHON, "-m", "pytest", "&&", "rm"), allowlist=ALLOWLIST)
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
