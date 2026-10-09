"""Disposable sensitive scanner. Only category names leave this process."""

import json
import math
import sys
import time

from evoagent.privacy.redaction import detect_sensitive


def main() -> None:
    max_bytes, cpu_ms = map(int, sys.argv[1:3])
    if sys.platform != "win32":
        import resource

        # Kernel backstop; the parent enforces millisecond CPU and wall deadlines.
        seconds = max(1, math.ceil(cpu_ms / 1000) + 1)
        resource.setrlimit(resource.RLIMIT_CPU, (seconds, seconds))
    # Imports on a mounted Windows workspace can be expensive in Linux Docker.
    # Startup/queue/read remain inside the wall deadline; CPU budget measures
    # the actual detection stage, not interpreter initialization.
    sys.stdout.write('{"ready":true}\n')
    sys.stdout.flush()
    raw = sys.stdin.buffer.read(max_bytes + 1)
    if len(raw) > max_bytes:
        raise SystemExit(2)
    started = time.process_time()
    categories = detect_sensitive(raw.decode("utf-8"))
    sys.stdout.write(
        json.dumps({"categories": categories, "cpu_ms": (time.process_time() - started) * 1000})
    )


if __name__ == "__main__":
    main()
