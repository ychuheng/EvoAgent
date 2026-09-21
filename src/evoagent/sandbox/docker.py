"""固定 Docker CLI 参数；只有控制器可以实例化这个驱动。"""

import asyncio
import json
import os
from contextlib import suppress
from pathlib import Path

from evoagent.tools.base import ToolExecutionError

LABEL = "evoagent.sandbox"


class SandboxError(ToolExecutionError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


async def bounded_output(process, limit):
    buffers = [bytearray(), bytearray()]
    total = 0

    async def read(stream, index):
        nonlocal total
        while chunk := await stream.read(16384):
            total += len(chunk)
            if total > limit:
                raise SandboxError("sandbox_output_limit")
            buffers[index].extend(chunk)

    tasks = [
        asyncio.create_task(read(process.stdout, 0)),
        asyncio.create_task(read(process.stderr, 1)),
    ]
    try:
        await asyncio.gather(*tasks)
        await process.wait()
        return bytes(buffers[0]), bytes(buffers[1])
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if process.returncode is None:
            with suppress(ProcessLookupError):
                process.kill()

            async def discard(stream):
                while await stream.read(16384):
                    pass

            async with asyncio.timeout(3):
                await asyncio.gather(discard(process.stdout), discard(process.stderr))
                await process.wait()


class DockerDriver:
    def __init__(self, executable="docker"):
        self.executable = executable

    async def spawn(self, *args, stdin=False):
        # 不继承 DOCKER_HOST/CONTEXT、代理、应用秘密；固定本机 Unix socket。
        environment = {
            "PATH": os.defpath,
            "DOCKER_HOST": "unix:///var/run/docker.sock",
            "DOCKER_CONFIG": "/opt/evoagent-empty-docker",
        }
        return await asyncio.create_subprocess_exec(
            self.executable,
            *args,
            env=environment,
            stdin=asyncio.subprocess.PIPE if stdin else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

    async def command(self, *args, limit=1048576, allow_missing=False):
        process = await self.spawn(*args)
        async with asyncio.timeout(15):
            stdout, _ = await bounded_output(process, limit)
        if process.returncode and not allow_missing:
            raise SandboxError("sandbox_docker_failed")
        return stdout

    async def preflight(self, spec):
        info = json.loads(await self.command("info", "--format", "{{json .}}"))
        if (
            info.get("OSType") != "linux"
            or not info.get("MemoryLimit")
            or not info.get("SwapLimit")
            or not info.get("CpuCfsQuota")
            or not info.get("PidsLimit")
            or not any("seccomp" in item for item in info.get("SecurityOptions", []))
        ):
            raise SandboxError("sandbox_kernel_unsupported")
        image = json.loads(await self.command("image", "inspect", spec.image))[0]
        if image.get("Config", {}).get("Volumes"):
            raise SandboxError("sandbox_image_volumes_forbidden")
        # 不自动 pull/build，部署者预先验收并加载 digest 镜像。
        if spec.image not in image.get("RepoDigests", []):
            raise SandboxError("sandbox_image_digest_mismatch")

    @staticmethod
    def create_args(name, spec, input_path, argv, *, stdio=False):
        args = [
            "create",
            "--name",
            name,
            "--label",
            f"{LABEL}=1",
            "--pull=never",
            "--user",
            "10001:10001",
            "--read-only",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges=true",
            "--network=none",
            "--ipc=private",
            "--memory",
            str(spec.memory_bytes),
            "--memory-swap",
            str(spec.memory_bytes),
            "--cpus",
            str(spec.cpus),
            "--pids-limit",
            str(spec.pids),
            "--ulimit",
            "nofile=128:128",
            "--ulimit",
            "core=0:0",
            "--log-driver=none",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,nodev,size=16777216,nr_inodes=128,mode=1777",
            "--tmpfs",
            "/output:rw,noexec,nosuid,nodev,size=16777216,nr_inodes=128,uid=10001,gid=10001,mode=0700",
            "--workdir",
            "/output",
        ]
        if input_path is not None:
            absolute = str(Path(input_path).resolve())
            if "," in absolute:
                raise SandboxError("sandbox_staging_invalid")
            args += [
                "--mount",
                f"type=bind,src={absolute},dst=/input,readonly,bind-propagation=rprivate",
            ]
        if spec.apparmor_profile:
            args += ["--security-opt", f"apparmor={spec.apparmor_profile}"]
        if stdio:
            args += [
                "--interactive",
                "--entrypoint",
                spec.stdio_command[0],
                spec.image,
                *spec.stdio_command[1:],
            ]
        else:
            args += [
                "--entrypoint",
                "/usr/local/bin/python",
                spec.image,
                "/opt/evoagent/runner.py",
                json.dumps(list(argv)),
                str(spec.output_bytes),
            ]
        return args

    async def create(self, name, spec, input_path, argv, *, stdio=False):
        await self.preflight(spec)
        await self.command(*self.create_args(name, spec, input_path, argv, stdio=stdio))
        detail = json.loads(await self.command("inspect", name))[0]
        host = detail["HostConfig"]
        if (
            host.get("NetworkMode") != "none"
            or not host.get("ReadonlyRootfs")
            or host.get("Privileged")
            or host.get("CapDrop") != ["ALL"]
            or not any(item.startswith("no-new-privileges") for item in host.get("SecurityOpt", []))
            or host.get("LogConfig", {}).get("Type") != "none"
            or host.get("Memory") != spec.memory_bytes
            or host.get("MemorySwap") != spec.memory_bytes
            or host.get("NanoCpus") != int(spec.cpus * 1e9)
            or host.get("PidsLimit") != spec.pids
            or detail["Config"].get("User") != "10001:10001"
        ):
            raise SandboxError("sandbox_spec_not_applied")
        return detail["Id"]

    async def start(self, name, *, stdin=False):
        return await self.spawn(
            "start", "--attach", *(["--interactive"] if stdin else []), name, stdin=stdin
        )

    async def remove(self, name):
        # rm 后再按精确名称查询；daemon 不可达不会误报已清理。
        await self.command("rm", "--force", name, allow_missing=True)
        names = (
            (
                await self.command(
                    "ps", "-a", "--filter", f"name=^/{name}$", "--format", "{{.Names}}"
                )
            )
            .decode()
            .splitlines()
        )
        if name in names:
            raise SandboxError("sandbox_cleanup_failed")

    async def managed(self):
        return (
            (
                await self.command(
                    "ps", "-a", "--filter", f"label={LABEL}=1", "--format", "{{.Names}}"
                )
            )
            .decode()
            .splitlines()
        )
