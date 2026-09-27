"""M1 项目根规范化与边界检查的单元测试（实施计划 §6 P-01/P-04 负向检查）。"""

import os
from pathlib import Path

import pytest

from evoagent.projects.schema import (
    ProjectRegistrationError,
    ensure_candidate_inside_root,
    normalize_root,
    resolve_inside_root,
)
from evoagent.tools.base import ToolExecutionError, ToolPermissionError


def test_normalize_root_rejects_relative_path(tmp_path: Path) -> None:
    with pytest.raises(ProjectRegistrationError, match="绝对路径"):
        normalize_root("some/relative/dir")


def test_normalize_root_rejects_missing_directory(tmp_path: Path) -> None:
    with pytest.raises(ProjectRegistrationError, match="不可用"):
        normalize_root(str(tmp_path / "does-not-exist"))


def test_normalize_root_rejects_file_target(tmp_path: Path) -> None:
    target = tmp_path / "file.txt"
    target.write_text("x", encoding="utf-8")

    with pytest.raises(ProjectRegistrationError, match="目录"):
        normalize_root(str(target))


def test_normalize_root_is_stable_and_absolute(tmp_path: Path) -> None:
    root = normalize_root(str(tmp_path / ".." / tmp_path.name))

    assert root.path.is_absolute()
    assert root.path == tmp_path.resolve()
    assert root.display == tmp_path.resolve().as_posix()


def test_resolve_inside_root_rejects_parent_traversal(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (tmp_path / "secret.txt").write_text("secret", encoding="utf-8")

    with pytest.raises(ToolPermissionError):
        resolve_inside_root(root, "../secret.txt")


def test_resolve_inside_root_rejects_absolute_outside_path(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")

    with pytest.raises(ToolPermissionError):
        resolve_inside_root(root, str(outside))


def test_resolve_inside_root_accepts_nested_file(tmp_path: Path) -> None:
    root = tmp_path / "project"
    nested = root / "src" / "pkg"
    nested.mkdir(parents=True)
    target = nested / "module.py"
    target.write_text("value = 1\n", encoding="utf-8")

    lexical, physical = resolve_inside_root(root, "src/pkg/./module.py")

    assert lexical == root / "src" / "pkg" / "module.py"
    assert physical == target.resolve()


def test_ensure_candidate_inside_root_requires_prefix(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    sibling = tmp_path / "project-other"
    sibling.mkdir()

    ensure_candidate_inside_root(root, root / "ok.txt")
    with pytest.raises(ToolPermissionError):
        ensure_candidate_inside_root(root, sibling / "escape.txt")


@pytest.mark.skipif(os.name != "nt", reason="junction 是 Windows 专有链接类型")
def test_resolve_inside_root_rejects_directory_junction_escape(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("secret", encoding="utf-8")

    junction = root / "linked"
    created = os.system(f'cmd /c mklink /J "{junction}" "{outside}" >nul 2>&1')
    if created != 0 or not junction.exists():
        pytest.skip("本机没有创建 junction 的权限")

    with pytest.raises((ToolPermissionError, ToolExecutionError)):
        resolve_inside_root(root, "linked/secret.txt")


def test_resolve_inside_root_rejects_symlinked_root(tmp_path: Path) -> None:
    """登记本身是链接时拒绝：授权根必须与 Agent 实际可见的根一致。"""

    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    try:
        link.symlink_to(real, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("本机没有创建符号链接的权限")

    with pytest.raises(ProjectRegistrationError, match="符号链接"):
        normalize_root(str(link))
