"""部署者控制传输配置，API 仅保存配置引用和审核元数据。"""

from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator

SDK_VERSION = "1.30.0"
PROTOCOL_VERSION = "2025-11-25"


class MCPError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class LaunchProfile(Contract):
    command: str
    args: tuple[str, ...] = Field(default=(), max_length=32)
    cwd: str | None = None
    trusted_fixture: Literal[True]

    @model_validator(mode="after")
    def controlled_command(self):
        if not Path(self.command).is_absolute() or Path(self.command).suffix.lower() in {
            ".bat",
            ".cmd",
            ".ps1",
            ".sh",
        }:
            raise ValueError("launch profile requires an absolute executable")
        if self.cwd and not Path(self.cwd).is_absolute():
            raise ValueError("launch cwd must be absolute")
        if any(len(arg) > 4096 for arg in self.args):
            raise ValueError("launch argument too long")
        return self


class HTTPProfile(Contract):
    url: str = Field(max_length=2048)
    # 只允许明确命名的本地 fixture；不放开任意私网/DNS 名称。
    local_fixture: bool = False

    @model_validator(mode="after")
    def controlled_endpoint(self):
        parts = urlsplit(self.url)
        if (
            not parts.hostname
            or parts.username is not None
            or parts.password is not None
            or parts.query
            or parts.fragment
            or parts.scheme not in {"http", "https"}
        ):
            raise ValueError("invalid MCP endpoint")
        _ = parts.port
        if self.local_fixture:
            if parts.hostname not in {"127.0.0.1", "::1"}:
                raise ValueError("fixture endpoint must be literal loopback")
        elif parts.scheme != "https":
            raise ValueError("remote endpoint requires https")
        return self


class MCPServerConfig(Contract):
    name: str = Field(min_length=1, max_length=128)
    transport: Literal["stdio", "streamable_http"]
    launch_profile_id: str | None = Field(default=None, max_length=128)
    endpoint_profile_id: str | None = Field(default=None, max_length=128)
    secret_ref: str | None = Field(default=None, max_length=128)
    connection_timeout: float = Field(default=10, ge=0.1, le=30)
    call_timeout: float = Field(default=10, ge=0.1, le=30)
    max_concurrency: Literal[1] = 1
    enabled: bool = False

    @model_validator(mode="after")
    def matching_transport(self):
        if self.transport == "stdio":
            valid = bool(self.launch_profile_id) and self.endpoint_profile_id is None
        else:
            valid = bool(self.endpoint_profile_id) and self.launch_profile_id is None
        if not valid:
            raise ValueError("transport requires exactly its deployment profile")
        return self


class ServerUpdate(Contract):
    expected_lock_version: int = Field(ge=0)
    config: MCPServerConfig


class ToolReview(Contract):
    expected_lock_version: int = Field(ge=0)
    tool_name: str = Field(min_length=1, max_length=128)
    approved: bool = False
    risk: Literal["R0", "R1", "R2", "R3"] = "R3"
    effect: Literal["read_only", "idempotent_write", "non_idempotent_write"] = (
        "non_idempotent_write"
    )
    reviewer: str = Field(min_length=1, max_length=128)
    reason: str = Field(min_length=1, max_length=2048)

    @model_validator(mode="after")
    def consistent_effect(self):
        if self.effect != "read_only" and self.risk in {"R0", "R1"}:
            raise ValueError("write effects require at least R2")
        return self
