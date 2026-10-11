"""Per-command pids v2 quota inside an operator-delegated subtree only."""

import asyncio
import os
import re
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from time import monotonic
from uuid import uuid4

from evoagent.tools.base import ToolExecutionError


class HardPidsUnavailable(ToolExecutionError):
    code = "project_command_hard_pids_unavailable"


def delegated_root(root):
    try:
        if not sys.platform.startswith("linux") or root is None:
            raise ValueError
        mount = Path("/sys/fs/cgroup").resolve(strict=True)
        path = Path(root).resolve(strict=True)
        if path == mount or not path.is_relative_to(mount):
            raise ValueError
        if (path / "cgroup.type").read_text().strip() != "domain":
            raise ValueError
        if "pids" not in (path / "cgroup.subtree_control").read_text().split():
            raise ValueError
        if not (path / "cgroup.controllers").is_file():
            raise ValueError
        return path
    except (OSError, ValueError):
        raise HardPidsUnavailable("requires a writable delegated cgroup v2 pids subtree") from None


class CommandPidsQuota:
    def __init__(self, root, maximum):
        if type(maximum) is not int or not 8 <= maximum <= 4096:
            raise HardPidsUnavailable("invalid per-command pids quota")
        self.root = delegated_root(root)
        self.path = self.root / ("evoagent-command-" + uuid4().hex)
        self.maximum = maximum

    def create(self):
        created = False
        try:
            self.path.mkdir()
            created = True
            # No parent controller changes or writes to existing cgroups.
            if not (self.path / "cgroup.kill").is_file():
                raise ValueError
            (self.path / "pids.max").write_text(str(self.maximum))
            if (self.path / "pids.max").read_text().strip() != str(self.maximum):
                raise ValueError
        except (OSError, ValueError):
            if created:
                self.path.rmdir()  # empty, owned group only; never recursive delete
            raise HardPidsUnavailable("per-command cgroup creation failed") from None
        return self

    def exceeded(self):
        try:
            events = dict(
                line.split() for line in (self.path / "pids.events").read_text().splitlines()
            )
            return int(events["max"]) > 0
        except (OSError, ValueError, KeyError):
            raise HardPidsUnavailable("per-command pids accounting unavailable") from None

    async def close(self):
        try:
            # Landlock denies command access to /sys; no descendant can move
            # itself out. kill also covers surviving children with closed pipes.
            (self.path / "cgroup.kill").write_text("1")
            deadline = monotonic() + 2
            while int((self.path / "pids.current").read_text()) != 0:
                if monotonic() >= deadline:
                    raise ValueError
                await asyncio.sleep(0.01)
            self.path.rmdir()
        except (OSError, ValueError):
            raise HardPidsUnavailable("per-command cgroup cleanup incomplete") from None


def join_command_group(path):
    """Trusted runner joins itself before loading sandbox or executing argv."""
    try:
        group = Path(path).resolve(strict=True)
        if not re.fullmatch(r"evoagent-command-[a-f0-9]{32}", group.name):
            raise ValueError
        root = delegated_root(group.parent)
        if group.parent != root or not (group / "cgroup.kill").is_file():
            raise ValueError
        maximum = int((group / "pids.max").read_text())
        if not 8 <= maximum <= 4096:
            raise ValueError
        (group / "cgroup.procs").write_text(str(os.getpid()))
        if str(os.getpid()) not in (group / "cgroup.procs").read_text().split():
            raise ValueError
        if int((group / "pids.current").read_text()) > maximum:
            # Migration can exceed pids.max; do not execute user argv then.
            raise ValueError
    except (OSError, ValueError):
        raise HardPidsUnavailable("cannot join per-command pids cgroup") from None


@asynccontextmanager
async def command_pids(root, maximum, *, required=False):
    if root is None:
        if required:
            raise HardPidsUnavailable("per-command hard pids quota is required")
        yield None
        return
    quota = CommandPidsQuota(root, maximum).create()
    try:
        yield quota
    finally:
        cleanup = asyncio.create_task(quota.close())
        try:
            await asyncio.shield(cleanup)
        except asyncio.CancelledError:
            await cleanup
            raise
