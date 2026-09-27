"""M1 项目发现/读取工具与项目上下文的单元测试（实施计划 §6 P-03/P-04/P-05）。

覆盖验收要求：路径越界与链接拒绝、大文件给出继续读取方式、二进制识别、
忽略规则可说明、目录扫描有上限、项目上下文不整仓塞入。
"""

from pathlib import Path

import pytest

from evoagent.projects.context import build_project_context
from evoagent.projects.schema import ProjectAuthorization
from evoagent.projects.service import ActiveProject
from evoagent.tools.base import ToolExecutionError, ToolPermissionError
from evoagent.tools.builtin.find_files import FindFilesArguments, FindFilesTool
from evoagent.tools.builtin.list_dir import ListDirArguments, ListDirTool
from evoagent.tools.builtin.project_file_read import (
    ProjectFileReadArguments,
    ProjectFileReadTool,
)
from evoagent.tools.builtin.search_text import SearchTextArguments, SearchTextTool


def make_project(root: Path) -> Path:
    (root / "src" / "pkg").mkdir(parents=True)
    (root / "src" / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (root / "src" / "pkg" / "store.py").write_text(
        "def put(key, value):\n    return {key: value}\n", encoding="utf-8"
    )
    (root / "README.md").write_text("# Sample\n\n项目说明。\n", encoding="utf-8")
    (root / "notes.txt").write_text("alpha beta\n", encoding="utf-8")
    (root / ".git").mkdir()
    (root / ".git" / "config").write_text("[core]\n", encoding="utf-8")
    (root / "node_modules").mkdir()
    (root / "node_modules" / "lib.js").write_text("module.exports = {}\n", encoding="utf-8")
    (root / "__pycache__").mkdir()
    (root / "__pycache__" / "store.cpython-313.pyc").write_bytes(b"\x00\x01")
    return root


@pytest.mark.asyncio
async def test_list_dir_reports_entries_and_explains_ignores(tmp_path: Path) -> None:
    root = make_project(tmp_path)

    result = await ListDirTool(root).invoke(ListDirArguments())

    assert "src/" in result and "README.md" in result
    assert ".git" not in result.split("被忽略")[0]
    assert "vcs_metadata" in result and "build_or_cache_directory" in result
    assert "忽略规则：" in result


@pytest.mark.asyncio
async def test_list_dir_enforces_entry_limit(tmp_path: Path) -> None:
    root = tmp_path
    for index in range(5):
        (root / f"f{index}.txt").write_text("x", encoding="utf-8")

    result = await ListDirTool(root).invoke(ListDirArguments(max_entries=2))

    assert "已达条目上限 2" in result


@pytest.mark.asyncio
async def test_list_dir_rejects_path_outside_root(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (tmp_path / "secret").mkdir()

    with pytest.raises(ToolPermissionError):
        await ListDirTool(root).invoke(ListDirArguments(path="../secret"))


@pytest.mark.asyncio
async def test_find_files_matches_name_and_relative_path(tmp_path: Path) -> None:
    root = make_project(tmp_path)

    by_name = await FindFilesTool(root).invoke(FindFilesArguments(pattern="*.py"))
    by_path = await FindFilesTool(root).invoke(FindFilesArguments(pattern="src/pkg/*.py"))

    assert "src/pkg/store.py" in by_name
    assert "src/pkg/store.py" in by_path
    # 命中列表里不得出现被忽略目录下的文件（忽略规则说明里会提到目录名，故只看命中段）。
    matches = by_name.split("已扫描文件：")[0]
    assert "node_modules" not in matches
    assert "已扫描文件：" in by_name


@pytest.mark.asyncio
async def test_find_files_skips_ignored_directories(tmp_path: Path) -> None:
    root = make_project(tmp_path)

    result = await FindFilesTool(root).invoke(FindFilesArguments(pattern="*.js"))

    assert "node_modules/lib.js" not in result


@pytest.mark.asyncio
async def test_search_text_returns_line_numbers(tmp_path: Path) -> None:
    root = make_project(tmp_path)

    result = await SearchTextTool(root).invoke(SearchTextArguments(query="def put"))

    assert "src/pkg/store.py:1: def put(key, value):" in result
    assert "已扫描文本文件：" in result


@pytest.mark.asyncio
async def test_search_text_enforces_match_limit(tmp_path: Path) -> None:
    root = tmp_path
    (root / "many.txt").write_text("\n".join("hit" for _ in range(10)), encoding="utf-8")

    result = await SearchTextTool(root).invoke(SearchTextArguments(query="hit", max_matches=3))

    assert "已达命中上限 3" in result
    assert result.count("- many.txt:") == 3


@pytest.mark.asyncio
async def test_project_file_read_returns_numbered_slice(tmp_path: Path) -> None:
    root = make_project(tmp_path)

    result = await ProjectFileReadTool(root).invoke(
        ProjectFileReadArguments(path="src/pkg/store.py", max_lines=1)
    )

    assert "总行数：2" in result
    assert "1: def put(key, value):" in result
    assert "用 start_line=2 继续" in result


@pytest.mark.asyncio
async def test_project_file_read_rejects_binary_and_oversized_lines(tmp_path: Path) -> None:
    root = tmp_path
    (root / "image.bin").write_bytes(b"\x00\x01\x02")
    (root / "short.txt").write_text("one\ntwo\n", encoding="utf-8")
    tool = ProjectFileReadTool(root)

    with pytest.raises(ToolExecutionError, match="二进制"):
        await tool.invoke(ProjectFileReadArguments(path="image.bin"))
    with pytest.raises(ToolExecutionError, match="超过文件总行数"):
        await tool.invoke(ProjectFileReadArguments(path="short.txt", start_line=9))


@pytest.mark.asyncio
async def test_project_file_read_ignores_symlink_target(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    link = root / "link.txt"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("本机没有创建符号链接的权限")

    with pytest.raises((ToolPermissionError, ToolExecutionError)):
        await ProjectFileReadTool(root).invoke(ProjectFileReadArguments(path="link.txt"))


def test_build_project_context_states_authorization_and_bounds(tmp_path: Path) -> None:
    root = make_project(tmp_path)
    project = ActiveProject(
        id=__import__("uuid").uuid4(),
        name="sample",
        root=root,
        authorization=ProjectAuthorization.READ,
        authorization_version=3,
    )

    context = build_project_context(project)

    assert "授权根：" in context and root.as_posix() in context
    assert "只读（read）" in context
    assert "授权版本：3" in context
    assert "list_dir" in context and "search_text" in context
    assert ".git" not in context
    assert "项目说明" in context  # README 摘录
    assert len(context) < 4_000
