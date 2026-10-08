"""项目命令的可观测性预检（K2 / §13.4）。

为什么需要：进程数配额靠父进程轮询 `/proc` 的**会话计数**实施（见
`commands.py::_watch_process_tree` 里对 `RLIMIT_NPROC` 的说明）。如果 `/proc` 读不到，
原先的实现返回 0 —— 那等于"看不见就当没超限"，配额形同虚设。本模块把这件事变成
**显式就绪判定**：要么证明现在能观测，要么明确报不可观测。

预检必须在**真正执行监控的那个环境**（实际 Worker 容器/UID/命名空间）里跑：
宿主上跑一次通过，不代表容器里能观测。所以它同时被 Worker 装配、`personal_preflight`
与命令执行前的运行时复查使用；`run_command()` 不信任预检结果，每次发起前自己再查一遍。

本模块**不**声称补齐内核 pids/cgroup 硬配额，也不声称验证了后代会话逃逸
（`setsid` 后的后代可以脱离统计）——那是 K2 的后续专项。
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from dataclasses import dataclass

from evoagent.projects.commands import (
    CommandUnobservableError,
    _session_process_count,
    ensure_observable_session,
)

REASON_READY = "ready"
REASON_DISABLED = "disabled"
REASON_TRUSTED_HOST = "trusted_host"
REASON_UNOBSERVABLE = "project_command_unobservable"

#: 预检总预算；超过就按不可观测处理，不无限等。
PROBE_BUDGET_SECONDS = 2.0
#: 探针子进程的存活时间，够被至少轮询到一次即可。
_PROBE_CHILD_SECONDS = 0.3
_PROBE_POLL_SECONDS = 0.02


@dataclass(frozen=True, slots=True)
class CommandReadinessResult:
    """结构化就绪结论；`ready=False` 时必须给出可读原因。"""

    ready: bool
    reason: str
    detail: str
    session_id: int | None = None
    session_process_count: int | None = None
    probe_process_counted: bool = False


class ProjectCommandReadinessProbe:
    """在当前环境核对 `/proc` 可观测性；不接收模型命令，也不执行用户参数。"""

    def __init__(self, *, budget_seconds: float = PROBE_BUDGET_SECONDS) -> None:
        self._budget_seconds = budget_seconds

    def check(self, settings) -> CommandReadinessResult:
        allowlist = tuple(getattr(settings, "project_command_allowlist", ()) or ())
        if not allowlist:
            # allowlist 为空 = 不运行任何命令：不要求这台机器支持命令执行。
            return CommandReadinessResult(
                ready=False,
                reason=REASON_DISABLED,
                detail="未配置任何允许执行的程序；本机不需要支持项目命令",
            )
        if getattr(settings, "trusted_host_mode", False):
            # Windows 可信主机模式没有 Linux 会话计数这套保证，单列而不假装等价。
            return CommandReadinessResult(
                ready=True,
                reason=REASON_TRUSTED_HOST,
                detail=(
                    "可信主机模式：以审批为边界，没有 Linux 会话进程数配额；"
                    "本模式不声称受进程树配额保护"
                ),
            )
        if not sys.platform.startswith("linux"):
            return CommandReadinessResult(
                ready=False,
                reason=REASON_UNOBSERVABLE,
                detail="项目命令需要 Linux Landlock/seccomp 环境，当前平台不支持",
            )
        deadline = time.monotonic() + self._budget_seconds
        try:
            session_id = ensure_observable_session(os.getpid())
            count = _session_process_count(session_id)
            counted = self._probe_child_counted(session_id, deadline)
        except CommandUnobservableError as error:
            return CommandReadinessResult(
                ready=False, reason=REASON_UNOBSERVABLE, detail=str(error)
            )
        if not counted:
            return CommandReadinessResult(
                ready=False,
                reason=REASON_UNOBSERVABLE,
                detail="预检子进程未被计入会话进程数：计数结果不可信",
                session_id=session_id,
                session_process_count=count,
            )
        return CommandReadinessResult(
            ready=True,
            reason=REASON_READY,
            detail="会话进程数可观测；进程树配额可实施",
            session_id=session_id,
            session_process_count=count,
            probe_process_counted=True,
        )

    def _probe_child_counted(self, session_id: int, deadline: float) -> bool:
        """起一个固定、离线、无用户参数的短命子进程，确认它真的被计入会话。"""

        argv = (sys.executable, "-c", f"import time; time.sleep({_PROBE_CHILD_SECONDS})")
        try:
            child = subprocess.Popen(  # noqa: S603 - 固定 argv，无用户输入
                argv,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except (OSError, ValueError):
            return False
        try:
            while time.monotonic() < deadline:
                if _session_process_count(child.pid) >= 1:
                    return True
                if child.poll() is not None:
                    break
                time.sleep(_PROBE_POLL_SECONDS)
            return False
        finally:
            if child.poll() is None:
                child.kill()
            child.wait()
