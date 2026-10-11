"""Fail closed without delegation; real kernel acceptance requires an isolated root."""

import os
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from evoagent.config import Settings
from evoagent.projects.cgroup_pids import CommandPidsQuota, HardPidsUnavailable, command_pids
from evoagent.projects.commands import CommandSpec, run_command
from evoagent.projects.readiness import REASON_HARD_PIDS, ProjectCommandReadinessProbe


@pytest.mark.parametrize("root", [None, "/", "/sys/fs/cgroup", "/tmp/not-delegated"])
def test_quota_refuses_missing_global_or_non_cgroup_roots(root):
    with pytest.raises(HardPidsUnavailable):
        CommandPidsQuota(root, 8).create()


async def test_required_missing_quota_cannot_downgrade_to_soft_monitor():
    with pytest.raises(HardPidsUnavailable):
        async with command_pids(None, 8, required=True):
            pytest.fail("unavailable hard quota entered")
    async with command_pids(None, 8, required=False) as quota:
        assert quota is None


def test_readiness_disables_commands_when_required_quota_has_no_delegation(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    settings = Settings(
        _env_file=None,
        project_command_allowlist=("python",),
        project_command_require_hard_pids=True,
    )
    result = ProjectCommandReadinessProbe().check(settings)
    assert not result.ready and result.reason == REASON_HARD_PIDS


@pytest.mark.skipif(os.name != "nt", reason="Windows trusted mode configuration")
def test_trusted_host_cannot_claim_linux_hard_quota():
    with pytest.raises(ValidationError, match="cannot provide Linux hard pids"):
        Settings(_env_file=None, trusted_host_mode=True, project_command_require_hard_pids=True)


async def test_real_kernel_pids_prevents_fork_and_cleans_only_its_group(tmp_path):
    root = os.getenv("EVOAGENT_TEST_CGROUP_ROOT")
    if not sys.platform.startswith("linux") or not root:
        pytest.skip("requires explicitly delegated isolated cgroup v2 test root")
    root = Path(root)
    # Do not accept a production command subtree as the test environment.
    if not root.name.startswith("evoagent-test-"):
        pytest.fail("test cgroup root must be an explicit evoagent-test-* subtree")
    before = set(root.iterdir())
    own_group = Path("/proc/self/cgroup").read_text()
    program = (
        "import os,time\nchildren=[]\ndenied=False\n"
        "try:\n"
        " for i in range(32):\n"
        "  try: pid=os.fork()\n"
        "  except BlockingIOError: denied=True; break\n"
        "  if pid==0: time.sleep(1); os._exit(0)\n"
        "  children.append(pid)\n"
        "finally:\n"
        " for pid in children: os.waitpid(pid,0)\n"
        "print('denied',denied)\n"
    )
    outcome = await run_command(
        tmp_path,
        CommandSpec(argv=(sys.executable, "-c", program)),
        allowlist=(Path(sys.executable).name,),
        timeout_seconds=10,
        output_limit=1024,
        max_processes=8,
        cgroup_root=root,
        require_hard_pids=True,
    )
    assert outcome.return_code == 0 and "denied True" in outcome.stdout
    assert outcome.process_limit_exceeded
    assert set(root.iterdir()) == before
    assert Path("/proc/self/cgroup").read_text() == own_group
