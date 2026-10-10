"""Real inherited seccomp boundaries, not a hard per-command pids guarantee."""

import sys
from pathlib import Path

import pytest

from evoagent.projects.commands import CommandSpec, run_command

pytestmark = pytest.mark.skipif(
    not sys.platform.startswith("linux"), reason="requires real Linux Landlock/seccomp"
)


@pytest.mark.parametrize("allow_network", [False, True])
async def test_grandchild_cannot_escape_session_or_group(tmp_path, allow_network):
    code = """import os, errno
pid = os.fork()
if pid == 0:
    for name, args in [('setsid', ()), ('setpgid', (0, 0))]:
        try:
            getattr(os, name)(*args)
        except OSError as error:
            if error.errno != errno.EPERM:
                os._exit(121)
            print('denied:' + name, flush=True)
        else:
            os._exit(122)
    os._exit(0)
_, status = os.waitpid(pid, 0)
raise SystemExit(os.waitstatus_to_exitcode(status))
"""
    python = Path(sys.executable).name
    result = await run_command(
        tmp_path,
        CommandSpec(argv=(python, "-c", code), allow_network=allow_network),
        allowlist=(python,),
        timeout_seconds=10,
        output_limit=2000,
        max_processes=16,
    )
    assert result.return_code == 0, result.stderr
    assert "denied:setsid" in result.stdout and "denied:setpgid" in result.stdout
    assert not result.timed_out
