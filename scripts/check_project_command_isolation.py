"""Run inside the application Linux image to verify real kernel command boundaries."""

import asyncio
import os
import sys
import tempfile
from pathlib import Path

from evoagent.projects.commands import CommandSpec, run_command


async def main() -> None:
    with tempfile.TemporaryDirectory(dir="/app/workspace") as directory:
        root = Path(directory)
        private = Path("/app/workspace/private-command-secret")
        private.write_text("private", encoding="utf-8")
        try:
            program = "\n".join(
                [
                    "import pathlib, socket",
                    "pathlib.Path('written.txt').write_text('done')",
                    "checks = [",
                    " ('network', lambda: socket.socket(socket.AF_INET)),",
                    " ('unix_connect', lambda: socket.socket(socket.AF_UNIX)",
                    "                          .connect('/tmp/unreachable.sock')),",
                    " ('secret', lambda: pathlib.Path('/app/workspace/private-command-secret')",
                    "                    .read_text()),",
                    " ('proc', lambda: pathlib.Path('/proc/1/environ').read_bytes()),",
                    "]",
                    "for label, action in checks:",
                    "    try:",
                    "        action()",
                    "    except PermissionError:",
                    "        print(label + ':blocked')",
                    "    else:",
                    "        print(label + ':OPEN')",
                ]
            )
            outcome = await run_command(
                root,
                CommandSpec(argv=(Path(sys.executable).name, "-c", program)),
                allowlist=(Path(sys.executable).name,),
                timeout_seconds=15,
                output_limit=10_000,
            )
            assert outcome.return_code == 0, outcome.stderr
            assert "network:blocked" in outcome.stdout, outcome.stdout
            assert "unix_connect:blocked" in outcome.stdout, outcome.stdout
            assert "secret:blocked" in outcome.stdout, outcome.stdout
            assert "proc:blocked" in outcome.stdout, outcome.stdout
            assert "OPEN" not in outcome.stdout, outcome.stdout
            assert (root / "written.txt").read_text() == "done"
            print("offline command: project write allowed")
            print("direct network, other files and proc denied")
        finally:
            private.unlink(missing_ok=True)


if __name__ == "__main__":
    if not sys.platform.startswith("linux"):
        raise SystemExit("Linux container required")
    os.environ["EVOAGENT_API_KEY"] = "must-not-reach-command"
    asyncio.run(main())
