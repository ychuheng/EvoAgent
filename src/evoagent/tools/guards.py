"""文件路径和网络地址的最低安全检查。"""

import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from evoagent.tools.base import ToolExecutionError, ToolPermissionError

AddressResolver = Callable[[str, int], Awaitable[Sequence[str]]]


class WorkspaceGuard:
    """确保解析后的现有文件仍位于指定 Workspace 中。"""

    def __init__(self, workspace: Path) -> None:
        self._workspace = workspace.expanduser().resolve(strict=False)

    @property
    def workspace(self) -> Path:
        return self._workspace

    def resolve_file(self, requested_path: str) -> Path:
        """解析普通路径和符号链接，并拒绝 Workspace 逃逸。"""

        if not requested_path.strip():
            raise ToolExecutionError("file path cannot be blank")
        try:
            raw_path = Path(requested_path).expanduser()
            candidate = raw_path if raw_path.is_absolute() else self._workspace / raw_path
            lexical_target = candidate.resolve(strict=False)
        except (OSError, ValueError) as error:
            raise ToolExecutionError("file path is invalid") from error

        self._ensure_inside_workspace(lexical_target)
        try:
            target = candidate.resolve(strict=True)
        except (OSError, ValueError) as error:
            raise ToolExecutionError("file does not exist or cannot be resolved") from error
        self._ensure_inside_workspace(target)
        if not target.is_file():
            raise ToolExecutionError("path does not point to a regular file")
        return target

    def _ensure_inside_workspace(self, target: Path) -> None:
        if not target.is_relative_to(self._workspace):
            raise ToolPermissionError("file access outside the workspace is not allowed")


class URLGuard:
    """解析目标主机并拒绝可能访问内部网络的 HTTP URL。"""

    def __init__(self, resolver: AddressResolver | None = None) -> None:
        self._resolver = resolver or self._resolve_addresses

    async def validate(self, url: str) -> str:
        """校验 URL 语法、凭据、主机名以及主机的全部 IP 地址。"""

        try:
            parts = urlsplit(url)
            port = parts.port
        except ValueError as error:
            raise ToolPermissionError("URL is invalid") from error

        scheme = parts.scheme.lower()
        if scheme not in {"http", "https"}:
            raise ToolPermissionError("only http and https URLs are allowed")
        if parts.username is not None or parts.password is not None:
            raise ToolPermissionError("URLs containing credentials are not allowed")
        host = parts.hostname
        if not host:
            raise ToolPermissionError("URL must include a host")
        normalized_host = host.rstrip(".").lower()
        if normalized_host == "localhost" or normalized_host.endswith(".localhost"):
            raise ToolPermissionError("localhost URLs are not allowed")

        addresses: Sequence[str]
        try:
            literal = ipaddress.ip_address(normalized_host)
        except ValueError:
            try:
                addresses = await self._resolver(
                    normalized_host,
                    port or (443 if scheme == "https" else 80),
                )
            except (OSError, UnicodeError) as error:
                raise ToolExecutionError("URL host could not be resolved") from error
            if not addresses:
                raise ToolExecutionError("URL host resolved to no addresses") from None
        else:
            addresses = (str(literal),)

        for raw_address in addresses:
            try:
                address = ipaddress.ip_address(raw_address)
            except ValueError as error:
                raise ToolExecutionError("resolver returned an invalid IP address") from error
            if not address.is_global:
                raise ToolPermissionError(
                    f"URL resolves to a non-public address: {address.compressed}"
                )

        path = parts.path or "/"
        return urlunsplit((scheme, parts.netloc, path, parts.query, ""))

    @staticmethod
    async def _resolve_addresses(host: str, port: int) -> tuple[str, ...]:
        infos = await asyncio.to_thread(
            socket.getaddrinfo,
            host,
            port,
            family=socket.AF_UNSPEC,
            type=socket.SOCK_STREAM,
        )
        return tuple(sorted({info[4][0] for info in infos}))
