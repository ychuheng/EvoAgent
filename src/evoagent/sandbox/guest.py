"""固定执行镜像的 PID 1；输出始终按不可信数据验收。仅依赖标准库。"""

import asyncio
import base64
import json
import os
import stat
import sys
from pathlib import Path

MAX_FILES = 32
MAX_FILE = 1048576
MAX_TOTAL = 8388608


def safe_name(name):
    return (
        bool(name)
        and len(name) <= 128
        and name not in {".", ".."}
        and not any(char in name for char in "/\\:\x00")
        and not any(ord(char) < 32 for char in name)
    )


def collect_outputs(root):
    files, total = [], 0
    entries = list(Path(root).iterdir())
    if len(entries) > MAX_FILES:
        raise ValueError("sandbox_file_limit")
    for path in sorted(entries):
        if not safe_name(path.name):
            raise ValueError("sandbox_artifact_path")
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as stream:
            metadata = os.fstat(stream.fileno())
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                raise ValueError("sandbox_artifact_type")
            content = stream.read(MAX_FILE + 1)
            total += len(content)
            if len(content) > MAX_FILE or total > MAX_TOTAL:
                raise ValueError("sandbox_artifact_size")
            files.append({"name": path.name, "data": base64.b64encode(content).decode("ascii")})
    return files


async def execute(argv, limit):
    process = await asyncio.create_subprocess_exec(
        *argv,
        cwd="/output",
        env={"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": "/tmp", "LANG": "C.UTF-8"},
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    buffers = [bytearray(), bytearray()]
    total = 0

    async def consume(stream, index):
        nonlocal total
        while chunk := await stream.read(16384):
            total += len(chunk)
            if total > limit:
                raise ValueError("sandbox_output_limit")
            buffers[index].extend(chunk)

    await asyncio.gather(consume(process.stdout, 0), consume(process.stderr, 1))
    await process.wait()
    return {
        "return_code": process.returncode,
        "stdout": buffers[0].decode("utf-8", "replace"),
        "stderr": buffers[1].decode("utf-8", "replace"),
        "files": collect_outputs("/output") if process.returncode == 0 else [],
    }


def main():
    try:
        result = asyncio.run(execute(json.loads(sys.argv[1]), int(sys.argv[2])))
    except Exception:
        result = {"error_code": "sandbox_guest_failed"}
    print(json.dumps(result, ensure_ascii=True), flush=True)
    # PID 1 退出；Docker 清理剩余进程，不只结束直接子进程。


if __name__ == "__main__":
    main()
