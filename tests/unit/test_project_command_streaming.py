"""Actual noisy subprocesses and cancelled Linux command ownership."""

import asyncio
import os
import sys
from pathlib import Path

import pytest

from evoagent.projects.commands import CommandSpec, _communicate_bounded, run_command


async def test_actual_dual_pipe_output_drains_without_retaining_full_body():
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        "import sys\nsys.stdout.buffer.write(b'x'*1048576)\nsys.stderr.buffer.write(b'y'*1048576)",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err, out_limited, err_limited = await asyncio.wait_for(
            _communicate_bounded(process, 1024),
            timeout=15,
        )
        assert process.returncode == 0
        assert out == b"x" * 1024 and err == b"y" * 1024
        assert out_limited and err_limited
    finally:
        if process.returncode is None:
            process.kill()
        await process.wait()


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux isolated command contract")
async def test_cancelled_real_project_command_reaps_its_process_before_returning(tmp_path):
    program = (
        "from pathlib import Path\nimport os,time\n"
        "Path('pid.txt').write_text(str(os.getpid()))\ntime.sleep(60)"
    )
    running = asyncio.create_task(
        run_command(
            tmp_path,
            CommandSpec(argv=(sys.executable, "-c", program)),
            allowlist=(Path(sys.executable).name,),
            timeout_seconds=90,
            output_limit=1024,
        )
    )
    try:
        async with asyncio.timeout(15):
            while not (tmp_path / "pid.txt").exists():
                if running.done():
                    await running
                    pytest.fail("project command ended before ready")
                await asyncio.sleep(0.02)
        pid = int((tmp_path / "pid.txt").read_text())
        running.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(running, timeout=5)
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)
    finally:
        running.cancel()
        await asyncio.gather(running, return_exceptions=True)


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux isolated command contract")
async def test_noisy_real_project_command_preserves_truncation_evidence(tmp_path):
    outcome = await run_command(
        tmp_path,
        CommandSpec(
            argv=(
                sys.executable,
                "-c",
                "import sys\nsys.stdout.buffer.write(b'x'*1048576)\n"
                "sys.stderr.buffer.write(b'y'*1048576)",
            )
        ),
        allowlist=(Path(sys.executable).name,),
        timeout_seconds=30,
        output_limit=1024,
    )
    assert outcome.return_code == 0
    assert outcome.stdout_truncated and outcome.stderr_truncated
    assert outcome.stdout.startswith("x" * 1024) and outcome.stderr.startswith("y" * 1024)
    assert len(outcome.stdout) < 1100 and len(outcome.stderr) < 1100
