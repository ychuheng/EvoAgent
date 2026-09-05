from pathlib import Path

import pytest

from evoagent.tools.base import ToolExecutionError, ToolPermissionError
from evoagent.tools.guards import URLGuard, WorkspaceGuard


async def public_resolver(host: str, port: int) -> tuple[str, ...]:
    return ("93.184.216.34",)


async def private_resolver(host: str, port: int) -> tuple[str, ...]:
    return ("10.0.0.8",)


def test_workspace_guard_allows_existing_file_inside_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    expected = workspace / "notes.txt"
    expected.write_text("hello", encoding="utf-8")

    resolved = WorkspaceGuard(workspace).resolve_file("notes.txt")

    assert resolved == expected.resolve()


def test_workspace_guard_rejects_parent_traversal(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "secret.txt"
    outside.write_text("secret", encoding="utf-8")

    with pytest.raises(ToolPermissionError, match="outside"):
        WorkspaceGuard(workspace).resolve_file("../secret.txt")


def test_workspace_guard_rejects_symbolic_link_escape(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "secret.txt"
    outside.write_text("secret", encoding="utf-8")
    link = workspace / "link.txt"
    try:
        link.symlink_to(outside)
    except OSError as error:
        pytest.skip(f"symbolic links are not available: {error}")

    with pytest.raises(ToolPermissionError, match="outside"):
        WorkspaceGuard(workspace).resolve_file("link.txt")


def test_workspace_guard_rejects_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ToolExecutionError, match="does not exist"):
        WorkspaceGuard(tmp_path).resolve_file("missing.txt")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "http://localhost/admin",
        "http://127.0.0.1/admin",
        "http://[::1]/admin",
        "http://169.254.169.254/latest/meta-data",
        "http://user:password@example.com/",
    ],
)
async def test_url_guard_rejects_unsafe_urls(url: str) -> None:
    with pytest.raises(ToolPermissionError):
        await URLGuard(public_resolver).validate(url)


@pytest.mark.asyncio
async def test_url_guard_rejects_domain_resolving_to_private_address() -> None:
    with pytest.raises(ToolPermissionError, match="non-public"):
        await URLGuard(private_resolver).validate("https://internal.example.test/data")


@pytest.mark.asyncio
async def test_url_guard_allows_public_http_url_and_removes_fragment() -> None:
    result = await URLGuard(public_resolver).validate("HTTPS://example.com/path?q=1#section")

    assert result == "https://example.com/path?q=1"
