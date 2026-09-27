"""M2 精确编辑的单元测试（实施计划 §7 E-01/E-03）。

覆盖验收要求：

- 原文件哈希前置条件：不匹配必须报冲突，不能覆盖同时发生的人手修改；
- 定位文本不唯一/不存在时报冲突，不做"尽力而为"的改写；
- 保留编码、BOM 与行尾（编辑后未改动部分的字节不变）；
- 多文件先全部校验再写盘；第二个文件失败时第一个文件必须回滚，且不得报告为全成功；
- 只读授权下写工具根本不注册；符号链接逃逸拒绝；目录删除拒绝。
"""

import codecs
from pathlib import Path

import pytest

from evoagent.projects.editing import (
    EditConflictError,
    EditRequest,
    LineRange,
    apply_edits,
    delete_path,
    move_path,
    sha256_bytes,
)
from evoagent.projects.schema import ProjectAuthorization
from evoagent.tools.base import ToolExecutionError, ToolPermissionError
from evoagent.tools.builtin.project_edit import (
    ApplyPatchArguments,
    ApplyPatchTool,
    DeleteFileArguments,
    DeleteFileTool,
    EditFileArguments,
    EditFileTool,
    MoveFileArguments,
    MoveFileTool,
    PatchFileSpec,
    project_edit_tools,
)


def make_repo(root: Path) -> Path:
    (root / "src").mkdir(parents=True)
    (root / "src" / "store.py").write_text(
        "def add(key, value):\n    return {key: value}\n", encoding="utf-8"
    )
    (root / "src" / "cli.py").write_text("from store import add\n", encoding="utf-8")
    return root


def test_edit_replaces_unique_text_and_reports_diff(tmp_path: Path) -> None:
    root = make_repo(tmp_path)

    outcome = apply_edits(
        root,
        [
            EditRequest(
                path="src/store.py",
                old_text="    return {key: value}",
                replacement="    return {key: value, 'ok': True}",
            )
        ],
    )

    assert outcome.outcomes[0].added_lines == 1
    assert outcome.outcomes[0].removed_lines == 1
    assert "-    return {key: value}" in outcome.outcomes[0].diff
    assert (
        (root / "src" / "store.py")
        .read_text(encoding="utf-8")
        .endswith("    return {key: value, 'ok': True}\n")
    )


def test_edit_requires_matching_hash_when_given(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    target = root / "src" / "store.py"
    stale = sha256_bytes(target.read_bytes())
    # 模拟"人手同时改了同一个文件"。
    target.write_text("def add(key, value):\n    return None\n", encoding="utf-8")

    with pytest.raises(EditConflictError, match="内容已变化"):
        apply_edits(
            root,
            [
                EditRequest(
                    path="src/store.py",
                    expected_sha256=stale,
                    old_text="    return None",
                    replacement="    return 1",
                )
            ],
        )
    assert target.read_text(encoding="utf-8") == "def add(key, value):\n    return None\n"


def test_edit_accepts_matching_hash(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    target = root / "src" / "store.py"
    current = sha256_bytes(target.read_bytes())

    outcome = apply_edits(
        root,
        [
            EditRequest(
                path="src/store.py",
                expected_sha256=current,
                old_text="def add(key, value):",
                replacement="def add(key, value, *, strict=False):",
            )
        ],
    )

    assert outcome.outcomes[0].new_sha256 == sha256_bytes(target.read_bytes())


def test_edit_rejects_ambiguous_or_missing_old_text(tmp_path: Path) -> None:
    root = tmp_path
    (root / "dup.txt").write_text("x\nx\n", encoding="utf-8")
    (root / "one.txt").write_text("only\n", encoding="utf-8")

    with pytest.raises(EditConflictError, match="出现 2 次"):
        apply_edits(root, [EditRequest(path="dup.txt", old_text="x", replacement="y")])
    with pytest.raises(EditConflictError, match="不存在"):
        apply_edits(root, [EditRequest(path="one.txt", old_text="nope", replacement="y")])


def test_edit_reports_out_of_range_line_range(tmp_path: Path) -> None:
    root = make_repo(tmp_path)

    with pytest.raises(EditConflictError, match="超出文件总行数"):
        apply_edits(
            root,
            [
                EditRequest(
                    path="src/store.py",
                    line_range=LineRange(start=9, end=10),
                    replacement="x",
                )
            ],
        )


def test_edit_preserves_crlf_line_endings(tmp_path: Path) -> None:
    root = tmp_path
    target = root / "windows.py"
    target.write_bytes(b"first\r\nsecond\r\nthird\r\n")

    outcome = apply_edits(
        root,
        [
            EditRequest(
                path="windows.py",
                old_text="second",
                replacement="SECOND",
            )
        ],
    )

    assert target.read_bytes() == b"first\r\nSECOND\r\nthird\r\n"
    assert outcome.outcomes[0].line_endings_after == {"crlf": 3, "lf": 0, "cr": 0}


def test_edit_preserves_utf8_bom(tmp_path: Path) -> None:
    root = tmp_path
    target = root / "bom.py"
    target.write_bytes(codecs.BOM_UTF8 + b"value = 1\n")

    apply_edits(root, [EditRequest(path="bom.py", old_text="value = 1", replacement="value = 2")])

    assert target.read_bytes() == codecs.BOM_UTF8 + b"value = 2\n"


def test_edit_creates_new_file_only_when_declared(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / "existing.txt").write_text("x\n", encoding="utf-8")

    with pytest.raises(EditConflictError, match="已存在"):
        apply_edits(
            root,
            [EditRequest(path="existing.txt", replacement="y", create=True)],
        )

    outcome = apply_edits(
        root,
        [EditRequest(path="created.txt", replacement="hello\n", create=True)],
    )
    assert outcome.outcomes[0].created is True
    assert (root / "created.txt").read_text(encoding="utf-8") == "hello\n"

    with pytest.raises(ToolExecutionError, match="目标目录不存在"):
        apply_edits(
            root,
            [EditRequest(path="missing/created.txt", replacement="hello\n", create=True)],
        )


def test_edit_rejects_unsupported_encoding(tmp_path: Path) -> None:
    root = tmp_path
    (root / "legacy.txt").write_bytes("caf\xe9\n".encode("latin-1"))

    with pytest.raises(ToolExecutionError, match="不是受支持的文本编码"):
        apply_edits(root, [EditRequest(path="legacy.txt", old_text="caf", replacement="coffee")])


def test_patch_rolls_back_when_a_later_file_conflicts(tmp_path: Path) -> None:
    """多文件：第二个前置条件失败时，第一个文件必须还原，且不报告全成功。"""

    root = make_repo(tmp_path)
    first = root / "src" / "store.py"
    second = root / "src" / "cli.py"
    original_first = first.read_bytes()
    original_second = second.read_bytes()

    with pytest.raises(EditConflictError):
        apply_edits(
            root,
            [
                EditRequest(path="src/store.py", old_text="def add(", replacement="def create("),
                EditRequest(
                    path="src/cli.py",
                    expected_sha256="0" * 64,
                    old_text="from store import add",
                    replacement="from store import create",
                ),
            ],
        )

    assert first.read_bytes() == original_first
    assert second.read_bytes() == original_second


def test_patch_applies_all_files_when_preconditions_hold(tmp_path: Path) -> None:
    root = make_repo(tmp_path)

    outcome = apply_edits(
        root,
        [
            EditRequest(path="src/store.py", old_text="def add(", replacement="def create("),
            EditRequest(
                path="src/cli.py",
                old_text="from store import add",
                replacement="from store import create",
            ),
        ],
    )

    assert [item.display for item in outcome.outcomes] == ["src/store.py", "src/cli.py"]
    assert "def create(" in (root / "src" / "store.py").read_text(encoding="utf-8")
    assert "import create" in (root / "src" / "cli.py").read_text(encoding="utf-8")


def test_headless_crlf_neutral_old_text_matches_crlf_file(tmp_path: Path) -> None:
    """模型给的 old_text 通常用 `\\n`；CRLF 文件应能匹配到同一段。"""

    root = tmp_path
    target = root / "win.py"
    target.write_bytes(b"def a():\r\n    return 1\r\n")

    apply_edits(
        root,
        [
            EditRequest(
                path="win.py",
                old_text="def a():\n    return 1",
                replacement="def a():\n    return 2",
            )
        ],
    )

    assert target.read_bytes() == b"def a():\r\n    return 2\r\n"


def test_move_and_delete_refuse_unsafe_targets(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    (root / "src" / "sub").mkdir()

    with pytest.raises(EditConflictError, match="已存在"):
        move_path(root, "src/store.py", "src/cli.py")
    with pytest.raises(ToolExecutionError, match="拒绝删除目录"):
        delete_path(root, "src/sub")
    with pytest.raises(ToolPermissionError):
        delete_path(root, "../outside.txt")

    source, destination = move_path(root, "src/store.py", "src/ledger.py")
    assert (source, destination) == ("src/store.py", "src/ledger.py")
    assert not (root / "src" / "store.py").exists()


@pytest.mark.asyncio
async def test_edit_tool_requires_read_write_authorization(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    read_only = EditFileTool(root, authorization=ProjectAuthorization.READ)

    with pytest.raises(ToolPermissionError, match="只读授权"):
        await read_only.invoke(
            EditFileArguments(path="src/store.py", old_text="def add(", replacement="def create(")
        )
    assert project_edit_tools(root, authorization=ProjectAuthorization.READ) == []


@pytest.mark.asyncio
async def test_edit_tool_dry_run_does_not_write(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    target = root / "src" / "store.py"
    before = target.read_bytes()
    tool = EditFileTool(root, authorization=ProjectAuthorization.READ_WRITE)

    result = await tool.invoke(
        EditFileArguments(
            path="src/store.py",
            old_text="def add(",
            replacement="def create(",
            dry_run=True,
        )
    )

    assert "dry_run（未写盘）" in result
    assert "-def add(key, value):" in result
    assert target.read_bytes() == before


@pytest.mark.asyncio
async def test_apply_patch_tool_reports_every_file(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    tool = ApplyPatchTool(root, authorization=ProjectAuthorization.READ_WRITE)

    result = await tool.invoke(
        ApplyPatchArguments(
            edits=[
                PatchFileSpec(path="src/store.py", old_text="def add(", replacement="def create("),
                PatchFileSpec(
                    path="src/cli.py",
                    old_text="from store import add",
                    replacement="from store import create",
                ),
            ]
        )
    )

    assert "文件数：2" in result
    assert "src/store.py" in result and "src/cli.py" in result


@pytest.mark.asyncio
async def test_delete_tool_refuses_directory_and_wrong_hash(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    (root / "src" / "sub").mkdir()
    tool = DeleteFileTool(root, authorization=ProjectAuthorization.READ_WRITE)

    with pytest.raises(ToolExecutionError, match="普通文件"):
        await tool.invoke(DeleteFileArguments(path="src/sub"))
    with pytest.raises(EditConflictError, match="内容已变化"):
        await tool.invoke(DeleteFileArguments(path="src/store.py", expected_sha256="0" * 64))


@pytest.mark.asyncio
async def test_move_tool_dry_run_and_apply(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    tool = MoveFileTool(root, authorization=ProjectAuthorization.READ_WRITE)

    preview = await tool.invoke(
        MoveFileArguments(source="src/store.py", destination="src/ledger.py", dry_run=True)
    )
    assert "dry_run（未移动）" in preview
    assert (root / "src" / "store.py").exists()

    applied = await tool.invoke(
        MoveFileArguments(source="src/store.py", destination="src/ledger.py")
    )
    assert "已移动" in applied
    assert (root / "src" / "ledger.py").exists()


def test_edit_rejects_symlink_target(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    outside = tmp_path / "outside.py"
    outside.write_text("value = 1\n", encoding="utf-8")
    link = root / "linked.py"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("本机没有创建符号链接的权限")

    with pytest.raises((ToolPermissionError, ToolExecutionError)):
        apply_edits(root, [EditRequest(path="linked.py", old_text="value = 1", replacement="v=2")])
