"""Worker 只使用已认证的控制器接口，没有 Docker socket 或 mount 参数。"""

import asyncio
from contextlib import suppress
from dataclasses import asdict
from uuid import uuid4

import httpx

from evoagent.sandbox.docker import SandboxError
from evoagent.sandbox.schema import SandboxRequest
from evoagent.skills.canonical import content_hash
from evoagent.tools.base import ToolPermissionError
from evoagent.tools.sandbox import ShellResult


class DisabledSandboxExecutor:
    implementation_version = "sandbox-disabled-v1"

    async def run(self, argv, input_artifact_ids=()):
        raise ToolPermissionError("shell requires an enabled Docker sandbox profile")


def controller_headers(settings):
    token = settings.sandbox_controller_token
    if token is None or len(token.get_secret_value()) < 32:
        raise SandboxError("sandbox_controller_unconfigured")
    url = httpx.URL(settings.sandbox_controller_url)
    if (
        url.scheme not in {"http", "https"}
        or url.username
        or url.password
        or url.query
        or url.fragment
    ):
        raise SandboxError("sandbox_controller_url_invalid")
    return {"Authorization": "Bearer " + token.get_secret_value()}


class ControllerExecutor:
    def __init__(self, settings, lease, *, transport=None):
        self.settings, self.lease, self.transport = settings, lease, transport
        self.profile = settings.shell_sandbox_profile
        self.spec = settings.sandbox_profiles.get(self.profile)
        if self.spec is None or self.spec.mode != "job":
            raise SandboxError("sandbox_profile_unavailable")
        self.profile_hash = content_hash(self.spec.model_dump(mode="json"))
        self.implementation_version = "docker-sandbox-v1:" + self.profile_hash

    async def run(self, argv, input_artifact_ids=()):
        identity = uuid4()
        request = SandboxRequest(
            execution_id=identity,
            profile=self.profile,
            profile_hash=self.profile_hash,
            argv=argv,
            input_artifact_ids=input_artifact_ids,
            **{
                key: value
                for key, value in asdict(self.lease).items()
                if key in {"task_id", "run_id", "owner", "epoch"}
            },
        )
        headers = controller_headers(self.settings)
        async with httpx.AsyncClient(
            base_url=self.settings.sandbox_controller_url,
            headers=headers,
            trust_env=False,
            follow_redirects=False,
            transport=self.transport,
            timeout=self.spec.timeout_seconds + 20,
        ) as client:
            try:
                async with client.stream(
                    "POST", "/executions", json=request.model_dump(mode="json")
                ) as response:
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > 8 * 1048576:
                            raise SandboxError("sandbox_output_limit")
                    if response.status_code != 200:
                        raise SandboxError("sandbox_controller_rejected")
                    import json

                    data = json.loads(body)
                    return ShellResult(
                        return_code=data["return_code"],
                        stdout=data["stdout"],
                        stderr=data["stderr"],
                        artifacts=tuple(data.get("artifacts", [])),
                    )
            except httpx.HTTPError:
                raise SandboxError("sandbox_controller_unavailable") from None
            finally:
                # HTTP 取消不等同于服务端任务取消；显式请求，失败仍有服务端租约/超时兜底。
                with suppress(Exception, asyncio.CancelledError):
                    await asyncio.shield(client.delete(f"/executions/{identity}", timeout=3))
