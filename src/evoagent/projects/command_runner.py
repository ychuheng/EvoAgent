"""Linux project-command child: apply kernel boundaries before executing argv.

This module is started in a fresh process.  Keeping the Landlock and seccomp setup
out of ``preexec_fn`` avoids running Python callbacks after fork in a threaded Worker.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import errno
import os
import resource
import sys
from pathlib import Path

from evoagent.projects.commands import command_readable_roots

_READ = (1 << 0) | (1 << 2) | (1 << 3)
_WRITE = sum(1 << bit for bit in (1, 2, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14))
_LANDLOCK_CREATE_RULESET = 444
_LANDLOCK_ADD_RULE = 445
_LANDLOCK_RESTRICT_SELF = 446
_LANDLOCK_RULE_PATH_BENEATH = 1
_PR_SET_NO_NEW_PRIVS = 38


class _Ruleset(ctypes.Structure):
    _fields_ = [("handled_access_fs", ctypes.c_uint64)]


class _PathBeneath(ctypes.Structure):
    _fields_ = [("allowed_access", ctypes.c_uint64), ("parent_fd", ctypes.c_int)]


class _SeccompArgCmp(ctypes.Structure):
    _fields_ = [
        ("arg", ctypes.c_uint),
        ("op", ctypes.c_int),
        ("datum_a", ctypes.c_uint64),
        ("datum_b", ctypes.c_uint64),
    ]


def _checked(value: int, label: str) -> int:
    if value < 0:
        raise OSError(ctypes.get_errno(), label)
    return value


def _landlock(project: Path, scratch: Path) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    libc.syscall.restype = ctypes.c_long
    libc.prctl.restype = ctypes.c_int
    abi = _checked(libc.syscall(_LANDLOCK_CREATE_RULESET, 0, 0, 1), "Landlock unavailable")
    # REFER (ABI 2) and TRUNCATE (ABI 3) must be handled to prevent rename or
    # truncation from silently escaping the policy on older kernels.
    if abi < 3:
        raise RuntimeError("Landlock ABI 3 or newer is required")
    ruleset = _Ruleset(_READ | _WRITE)
    ruleset_fd = _checked(
        libc.syscall(_LANDLOCK_CREATE_RULESET, ctypes.byref(ruleset), ctypes.sizeof(ruleset), 0),
        "Landlock ruleset creation failed",
    )

    def allow(path: Path, access: int) -> None:
        if not path.exists():
            return
        if not path.is_dir():
            access &= (1 << 0) | (1 << 1) | (1 << 2) | (1 << 14)
        fd = os.open(path, os.O_PATH | os.O_CLOEXEC)
        try:
            rule = _PathBeneath(access, fd)
            _checked(
                libc.syscall(
                    _LANDLOCK_ADD_RULE,
                    ruleset_fd,
                    _LANDLOCK_RULE_PATH_BENEATH,
                    ctypes.byref(rule),
                    0,
                ),
                f"Landlock rule failed: {path}",
            )
        finally:
            os.close(fd)

    try:
        # 系统目录 + **应用自己的解释器前缀**：CI 上 Python 装在 /opt/hostedtoolcache，
        # 不放行它的话，下面 execvpe 目标解释器会 EACCES（详见 command_readable_roots）。
        for path in command_readable_roots():
            allow(path, _READ)
        for path in (
            "/etc/ld.so.cache",
            "/etc/nsswitch.conf",
            "/etc/passwd",
            "/etc/group",
            "/etc/localtime",
            "/etc/hosts",
            "/etc/resolv.conf",
            "/dev/urandom",
            "/dev/random",
        ):
            allow(Path(path), _READ)
        allow(Path("/dev/null"), _READ | (1 << 1))
        allow(project, _READ | _WRITE)
        allow(scratch, _READ | _WRITE)
        _checked(libc.prctl(_PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0), "no_new_privs failed")
        _checked(
            libc.syscall(_LANDLOCK_RESTRICT_SELF, ruleset_fd, 0), "Landlock restriction failed"
        )
    finally:
        os.close(ruleset_fd)


def _load_seccomp() -> ctypes.CDLL:
    """加载 libseccomp：**先直接 dlopen 已知 soname**，最后才退回 `find_library`。

    `ctypes.util.find_library` 在 Linux 上会去跑 `ldconfig`/`gcc`——也就是**沙箱初始化
    自己需要 fork**。这在受限环境里很脆：一旦本进程带着偏紧的资源上限（或 Landlock 下
    禁止执行这些辅助程序），它会静默返回 `None`，命令以"libseccomp is required"这种
    误导性理由失败（实测：`RLIMIT_NPROC` 调紧时必现）。dlopen 不 fork，因此先试它。
    """

    for soname in ("libseccomp.so.2", "libseccomp.so"):
        try:
            return ctypes.CDLL(soname, use_errno=True)
        except OSError:
            continue
    library = ctypes.util.find_library("seccomp")
    if not library:
        raise RuntimeError("libseccomp is required for project commands")
    return ctypes.CDLL(library, use_errno=True)


def _block_network(*, offline: bool = True) -> None:
    seccomp = _load_seccomp()
    seccomp.seccomp_init.argtypes = [ctypes.c_uint32]
    seccomp.seccomp_init.restype = ctypes.c_void_p
    seccomp.seccomp_syscall_resolve_name.argtypes = [ctypes.c_char_p]
    seccomp.seccomp_syscall_resolve_name.restype = ctypes.c_int
    seccomp.seccomp_rule_add_array.argtypes = [
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_int,
        ctypes.c_uint,
        ctypes.POINTER(_SeccompArgCmp),
    ]
    seccomp.seccomp_rule_add_array.restype = ctypes.c_int
    seccomp.seccomp_load.argtypes = [ctypes.c_void_p]
    seccomp.seccomp_load.restype = ctypes.c_int
    seccomp.seccomp_release.argtypes = [ctypes.c_void_p]
    context = seccomp.seccomp_init(0x7FFF0000)  # SCMP_ACT_ALLOW
    if not context:
        raise RuntimeError("seccomp initialization failed")
    try:
        # The parent creates the command's session before this process starts.
        # Descendants must remain in its process group so killpg and /proc
        # observation still cover double-forked children, even with networking.
        for name in (b"setsid", b"setpgid", b"unshare", b"setns"):
            call = seccomp.seccomp_syscall_resolve_name(name)
            if (
                call < 0
                or seccomp.seccomp_rule_add_array(context, 0x00050000 | errno.EPERM, call, 0, None)
                != 0
            ):
                raise RuntimeError("seccomp process containment installation failed")
        if offline:
            socket_call = seccomp.seccomp_syscall_resolve_name(b"socket")
            if socket_call < 0:
                raise RuntimeError("socket syscall unavailable")
            # AF_UNIX socketpair remains usable; connect is denied.
            not_unix = _SeccompArgCmp(0, 1, 1, 0)
            result = seccomp.seccomp_rule_add_array(
                context, 0x00050000 | errno.EPERM, socket_call, 1, ctypes.byref(not_unix)
            )
            connect_call = seccomp.seccomp_syscall_resolve_name(b"connect")
            if connect_call < 0:
                raise RuntimeError("connect syscall unavailable")
            connect_result = seccomp.seccomp_rule_add_array(
                context, 0x00050000 | errno.EPERM, connect_call, 0, None
            )
            if result != 0 or connect_result != 0:
                raise RuntimeError("seccomp network filter installation failed")
        if seccomp.seccomp_load(context) != 0:
            raise RuntimeError("seccomp filter installation failed")
    finally:
        seccomp.seccomp_release(context)


def main() -> int:
    try:
        separator = sys.argv.index("--")
        project = Path(sys.argv[1]).resolve(strict=True)
        scratch = Path(sys.argv[2]).resolve(strict=True)
        cpu_seconds = int(sys.argv[3])
        memory_bytes = int(sys.argv[4])
        offline = sys.argv[5] == "offline"
        command = sys.argv[separator + 1 :]
        if not command or separator not in (6, 7) or not sys.platform.startswith("linux"):
            raise RuntimeError("invalid command runner invocation")
        if separator == 7:
            from evoagent.projects.cgroup_pids import join_command_group

            join_command_group(sys.argv[6])
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))
        resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))
        _landlock(project, scratch)
        _block_network(offline=offline)
        os.execvpe(command[0], command, os.environ)
    except Exception as error:
        print(f"EVOAGENT_COMMAND_SETUP_FAILED: {error}", file=sys.stderr)
        return 125


if __name__ == "__main__":
    raise SystemExit(main())
