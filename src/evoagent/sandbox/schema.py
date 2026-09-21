"""固定容器规格与仅由可信控制器消费的执行请求。"""

from typing import Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from evoagent.tools.sandbox import ShellResult


class SandboxSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    image: str = Field(pattern=r"^[a-zA-Z0-9./:_-]+@sha256:[0-9a-f]{64}$")
    mode: Literal["job", "stdio"] = "job"
    allowed_executables: tuple[str, ...] = ("/usr/local/bin/python",)
    stdio_command: tuple[str, ...] = ()
    timeout_seconds: float = Field(default=30, ge=1, le=300)
    memory_bytes: int = Field(default=268435456, ge=67108864, le=536870912)
    cpus: float = Field(default=1.0, gt=0, le=2)
    pids: int = Field(default=64, ge=8, le=128)
    output_bytes: int = Field(default=1048576, ge=1024, le=4194304)
    apparmor_profile: str | None = Field(default=None, pattern=r"^[a-zA-Z0-9_.-]+$")

    @model_validator(mode="after")
    def controlled_entrypoints(self):
        if any(not item.startswith("/") for item in self.allowed_executables):
            raise ValueError("allowed executables must be absolute Linux paths")
        if self.mode == "stdio" and (
            not self.stdio_command or not self.stdio_command[0].startswith("/")
        ):
            raise ValueError("stdio profile requires an absolute entrypoint")
        return self

    @field_validator("allowed_executables", "stdio_command")
    @classmethod
    def bounded_argv(cls, value):
        if len(value) > 64 or sum(len(arg.encode()) for arg in value) > 16384:
            raise ValueError("argv exceeds limit")
        if any("\x00" in arg for arg in value):
            raise ValueError("invalid argv")
        return value


class SandboxRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    execution_id: UUID
    task_id: UUID
    run_id: UUID
    owner: str = Field(min_length=1, max_length=128)
    epoch: int = Field(ge=1)
    profile: str = Field(min_length=1, max_length=128)
    profile_hash: str
    argv: tuple[str, ...] = Field(min_length=1, max_length=64)
    input_artifact_ids: tuple[UUID, ...] = Field(default=(), max_length=32)

    @field_validator("argv")
    @classmethod
    def bounded_argv(cls, value):
        return SandboxSpec.bounded_argv(value)


class SandboxExecutor(Protocol):
    async def run(
        self, argv: tuple[str, ...], input_artifact_ids: tuple[UUID, ...] = ()
    ) -> ShellResult: ...
