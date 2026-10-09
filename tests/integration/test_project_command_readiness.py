"""K2 / §13.4：项目命令的可观测性预检与运行时复查。

三件事必须分开成立，任何一件都不能替代另一件：

1. 计数要么给出数字、要么明确报不可观测——**不用 0 假装健康**；
2. 宿主预检**不替代**实际 Worker 容器/UID/命名空间里的预检；
3. 启动时通过一次**不替代**运行中的复查。

`/proc` 的失败用假 `open`/`listdir` 模拟，因此在任何平台上都能验证计数逻辑；
真正需要 Linux 的用例（探针子进程真的被计数）显式跳过。
"""

from __future__ import annotations

import asyncio
import errno
import io
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from evoagent.config import Settings
from evoagent.projects import commands
from evoagent.projects.commands import (
    CommandUnobservableError,
    _session_process_count,
    _watch_process_tree,
    ensure_observable_session,
    run_command,
)
from evoagent.projects.readiness import (
    REASON_DISABLED,
    REASON_READY,
    REASON_TRUSTED_HOST,
    REASON_UNOBSERVABLE,
    ProjectCommandReadinessProbe,
)

SESSION = 4_242


def _stat(pid: int, session: int, comm: str = "prog") -> bytes:
    return f"{pid} ({comm}) S 1 {pid} {session} 0 -1 0".encode()


def _stub_signal(monkeypatch) -> None:
    """`_watch_process_tree` 是 Linux 会话计数路径；Windows 上没有 SIGKILL，
    这里补一个常量而不是改生产代码去迁就测试。"""

    if not hasattr(commands.signal, "SIGKILL"):
        monkeypatch.setattr(
            commands,
            "signal",
            SimpleNamespace(SIGKILL=9, SIGTERM=15),
            raising=False,
        )


def _fake_proc(monkeypatch, entries: dict[str, object]) -> None:
    """把模块内的 open/listdir 换成假 /proc；异常值按原样抛出。"""

    def fake_open(path, *_args, **_kwargs):
        pid = str(path).split("/")[2]
        value = entries.get(pid)
        if isinstance(value, BaseException):
            raise value
        if value is None:
            raise FileNotFoundError(path)
        return io.BytesIO(value)  # type: ignore[arg-type]

    monkeypatch.setattr(commands, "open", fake_open, raising=False)
    monkeypatch.setattr(commands.os, "listdir", lambda _path: list(entries))


def test_count_reads_session_field_after_command_name(monkeypatch) -> None:
    """comm 含空格与括号时也必须取到正确的 session 字段。"""

    _fake_proc(
        monkeypatch,
        {
            "10": _stat(10, SESSION, comm="a b) c"),
            "11": _stat(11, SESSION),
            "12": _stat(12, SESSION + 1),
            "not-a-pid": b"",
        },
    )

    assert _session_process_count(SESSION) == 2


def test_empty_proc_is_a_valid_observation(monkeypatch) -> None:
    _fake_proc(monkeypatch, {})

    assert _session_process_count(SESSION) == 0


def test_entry_that_exits_while_reading_is_skipped(monkeypatch) -> None:
    """恰好在 listdir 与 open 之间退出的条目是允许跳过的竞态。"""

    _fake_proc(
        monkeypatch,
        {"10": _stat(10, SESSION), "11": FileNotFoundError("/proc/11/stat")},
    )

    assert _session_process_count(SESSION) == 1


def test_permission_denied_is_not_silently_ignored(monkeypatch) -> None:
    _fake_proc(
        monkeypatch,
        {"10": _stat(10, SESSION), "11": PermissionError("/proc/11/stat")},
    )

    with pytest.raises(CommandUnobservableError):
        _session_process_count(SESSION)


def test_reaped_entry_after_open_is_skipped(monkeypatch) -> None:
    """stat 已打开但进程随后被回收：Linux read 返回 ESRCH，而不是 ENOENT。"""

    _fake_proc(monkeypatch, {"10": _stat(10, SESSION), "11": b""})
    original_open = commands.open

    class ReapedStat(io.BytesIO):
        def read(self, *_args, **_kwargs):
            raise ProcessLookupError(errno.ESRCH, "No such process")

    def open_with_reaped_entry(path, *args, **kwargs):
        if str(path) == "/proc/11/stat":
            return ReapedStat()
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(commands, "open", open_with_reaped_entry)
    assert _session_process_count(SESSION) == 1


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="需要真实 /proc ESRCH 语义")
def test_real_stat_read_after_process_reaping(monkeypatch) -> None:
    """用实际已回收进程的 stat 描述符，确定性复现 CI 中的退出竞态。"""

    process = subprocess.Popen(
        [sys.executable, "-c", "import sys; sys.stdin.buffer.read(1)"],
        stdin=subprocess.PIPE,
    )
    try:
        with open(f"/proc/{process.pid}/stat", "rb") as handle:
            assert process.stdin is not None
            process.stdin.close()
            process.wait(timeout=5)
            with pytest.raises(ProcessLookupError) as raised:
                handle.read()
            assert raised.value.errno == errno.ESRCH
            monkeypatch.setattr(commands.os, "listdir", lambda _path: [str(process.pid)])

            # 不关闭真实描述符；计数器只负责本次读取。
            class HeldStat:
                def __enter__(self):
                    return handle

                def __exit__(self, *_args):
                    return False

            monkeypatch.setattr(commands, "open", lambda *_a, **_kw: HeldStat(), raising=False)
            assert _session_process_count(process.pid) == 0
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)


def test_unparsable_live_entry_is_not_silently_ignored(monkeypatch) -> None:
    _fake_proc(monkeypatch, {"10": _stat(10, SESSION), "11": b"garbage"})

    with pytest.raises(CommandUnobservableError):
        _session_process_count(SESSION)


def test_unlistable_proc_raises_instead_of_returning_zero(monkeypatch) -> None:
    """K2 的核心缺陷：`/proc` 读不到时返回 0 等于"看不见就当没超限"。"""

    def boom(_path):
        raise PermissionError("/proc")

    monkeypatch.setattr(commands.os, "listdir", boom)

    with pytest.raises(CommandUnobservableError):
        _session_process_count(SESSION)


def test_self_check_rejects_unparsable_own_stat(monkeypatch) -> None:
    monkeypatch.setattr(
        commands, "open", lambda *_a, **_k: io.BytesIO(b"not a stat line"), raising=False
    )

    with pytest.raises(CommandUnobservableError):
        ensure_observable_session(1234)


def test_self_check_rejects_zero_session_count(monkeypatch) -> None:
    _fake_proc(monkeypatch, {str(1234): _stat(1234, SESSION)})
    monkeypatch.setattr(commands, "_session_process_count", lambda _sid: 0)

    with pytest.raises(CommandUnobservableError, match="不可信"):
        ensure_observable_session(1234)


@pytest.mark.asyncio
async def test_watcher_terminates_the_group_when_observability_is_lost(monkeypatch) -> None:
    """监控中失效：杀掉所控进程组并如实返回原因（不能当成"没超限"）。"""

    killed: list[int] = []
    _stub_signal(monkeypatch)
    monkeypatch.setattr(commands.os, "killpg", lambda pid, _sig: killed.append(pid), raising=False)

    def unobservable(_pid):
        raise CommandUnobservableError("假装的 /proc 失败")

    monkeypatch.setattr(commands, "_session_process_count", unobservable)
    process = SimpleNamespace(pid=9_999)
    drain = asyncio.get_running_loop().create_future()

    reason = await _watch_process_tree(process, max_processes=8, drain=drain)

    assert reason == "unobservable"
    assert killed == [9_999]
    drain.cancel()


@pytest.mark.asyncio
async def test_watcher_kills_on_process_limit(monkeypatch) -> None:
    killed: list[int] = []
    _stub_signal(monkeypatch)
    monkeypatch.setattr(commands.os, "killpg", lambda pid, _sig: killed.append(pid), raising=False)
    monkeypatch.setattr(commands, "_session_process_count", lambda _pid: 99)
    process = SimpleNamespace(pid=1_234)
    drain = asyncio.get_running_loop().create_future()

    reason = await _watch_process_tree(process, max_processes=8, drain=drain)

    assert reason == "process_limit"
    assert killed == [1_234]
    drain.cancel()


@pytest.mark.asyncio
async def test_runtime_recheck_cannot_be_replaced_by_a_passing_preflight(monkeypatch) -> None:
    """宿主/启动预检返回就绪，也必须每次发起前自己再查一遍。"""

    monkeypatch.setattr(sys, "platform", "linux")

    def unobservable(_pid):
        raise CommandUnobservableError("发起前检查失败")

    monkeypatch.setattr(commands, "ensure_observable_session", unobservable)

    async def must_not_spawn(*_args, **_kwargs):  # pragma: no cover - 走到这里就是失败
        raise AssertionError("不可观测时不得启动子进程")

    monkeypatch.setattr(commands.asyncio, "create_subprocess_exec", must_not_spawn)

    with pytest.raises(CommandUnobservableError):
        await run_command(
            Path("."),
            commands.CommandSpec(argv=("python", "-V")),
            allowlist=("python",),
            timeout_seconds=5,
            output_limit=1_000,
        )


def test_probe_reports_disabled_without_allowlist() -> None:
    result = ProjectCommandReadinessProbe().check(Settings(_env_file=None))

    assert result.ready is False
    assert result.reason == REASON_DISABLED
    assert result.probe_process_counted is False


def test_probe_lists_trusted_host_separately() -> None:
    """Windows 可信主机没有 Linux 会话计数这套保证，不假装等价。"""

    # 这里只测试预检的模式说明；真实 Settings 的平台准入在下一条用例独立验证。
    settings = SimpleNamespace(project_command_allowlist=("git",), trusted_host_mode=True)
    result = ProjectCommandReadinessProbe().check(settings)

    assert result.ready is True
    assert result.reason == REASON_TRUSTED_HOST
    assert "没有 Linux 会话进程数配额" in result.detail


@pytest.mark.skipif(sys.platform == "win32", reason="验证非 Windows 的配置拒绝")
def test_settings_rejects_trusted_host_on_non_windows() -> None:
    with pytest.raises(ValueError, match="trusted host mode requires a Windows process"):
        Settings(_env_file=None, project_command_allowlist=("git",), trusted_host_mode=True)


def test_probe_reports_unobservable_instead_of_healthy(monkeypatch) -> None:
    settings = Settings(_env_file=None, project_command_allowlist=("git",))
    monkeypatch.setattr(sys, "platform", "linux")

    def unobservable(_pid):
        raise CommandUnobservableError("假装的 /proc 失败")

    monkeypatch.setattr("evoagent.projects.readiness.ensure_observable_session", unobservable)

    result = ProjectCommandReadinessProbe().check(settings)

    assert result.ready is False
    assert result.reason == REASON_UNOBSERVABLE
    assert "假装的 /proc 失败" in result.detail


def test_probe_rejects_uncounted_child_process(monkeypatch) -> None:
    """计数结果里看不到自己起的探针子进程 → 计数不可信，不登记就绪。"""

    settings = Settings(_env_file=None, project_command_allowlist=("git",))
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(
        "evoagent.projects.readiness.ensure_observable_session", lambda _pid: SESSION
    )
    monkeypatch.setattr("evoagent.projects.readiness._session_process_count", lambda _sid: 1)
    monkeypatch.setattr(
        ProjectCommandReadinessProbe, "_probe_child_counted", lambda *_a, **_k: False
    )

    result = ProjectCommandReadinessProbe().check(settings)

    assert result.ready is False
    assert result.reason == REASON_UNOBSERVABLE
    assert "不可信" in result.detail


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="需要真实 /proc")
def test_probe_ready_on_a_real_linux_environment() -> None:
    settings = Settings(_env_file=None, project_command_allowlist=("git",))

    result = ProjectCommandReadinessProbe().check(settings)

    assert result.ready is True
    assert result.reason == REASON_READY
    assert result.probe_process_counted is True
    assert (result.session_process_count or 0) >= 1
