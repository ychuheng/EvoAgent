"""Verify the development fixture's failing test runs through the real command tool."""

import asyncio
from pathlib import Path

from evoagent.projects.commands import CommandSpec, run_command


async def main() -> None:
    outcome = await run_command(
        Path("/app/projects"),
        CommandSpec(argv=("python", "-m", "pytest", "-q")),
        allowlist=("python",),
        timeout_seconds=30,
        output_limit=20_000,
        environment_extra={"PYTHONPATH": "src"},
    )
    assert outcome.return_code == 1, (outcome.return_code, outcome.stderr, outcome.stdout)
    assert "saved!" in outcome.stdout, (outcome.stdout, outcome.stderr)
    assert "PermissionError" not in outcome.stderr, outcome.stderr
    print("failing project test reached its real assertion through Landlock/seccomp")


if __name__ == "__main__":
    asyncio.run(main())
