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
from uuid import uuid4

import pytest

from evoagent.core.events import InMemoryEventSink
from evoagent.core.models import ToolCall, ToolResultStatus
from evoagent.projects.editing import (
    EditConflictError,
    EditRequest,
    LineRange,
    RedactedEditError,
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
from evoagent.tools.executor import ToolExecutor
from evoagent.tools.registry import ToolRegistry


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
        await tool.invoke(DeleteFileArguments(path="src/sub", expected_sha256="0" * 64))
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


# --- §2.3 编辑门禁：不能用有损视图回写原文 ---------------------------------------

SECRET_CONFIG = "service:\n  token: sk-abcdefghijklmnopqrst\n  retries: 3\n"


def make_secret_repo(root: Path) -> Path:
    (root / "config").mkdir(parents=True)
    (root / "config" / "app.yml").write_text(SECRET_CONFIG, encoding="utf-8")
    return root


def test_line_range_covering_hidden_span_is_refused(tmp_path: Path) -> None:
    root = make_secret_repo(tmp_path)
    target = root / "config" / "app.yml"
    before = target.read_bytes()

    with pytest.raises(RedactedEditError) as error:
        apply_edits(
            root,
            [
                EditRequest(
                    path="config/app.yml",
                    line_range=LineRange(start=2, end=2),
                    replacement="  token: 已轮换",
                )
            ],
        )

    assert error.value.code == "redacted_edit_requires_review"
    # 失败前后磁盘 bytes 必须完全相同
    assert target.read_bytes() == before
    # 错误信息不得泄露命中的原值
    assert "sk-abcdefghijklmnopqrst" not in str(error.value)


def test_old_text_matching_a_hidden_span_is_refused(tmp_path: Path) -> None:
    """不靠模型自觉：即使它给出的 old_text 真的匹配，命中隐藏区间也拒绝。"""

    root = make_secret_repo(tmp_path)

    with pytest.raises(RedactedEditError):
        apply_edits(
            root,
            [
                EditRequest(
                    path="config/app.yml",
                    old_text="  token: sk-abcdefghijklmnopqrst",
                    replacement="  token: 已轮换",
                )
            ],
        )


def test_partial_overlap_with_hidden_span_is_refused(tmp_path: Path) -> None:
    """F1 回归：old_text 只取被隐藏值的**子串**时也必须拒绝。

    片段本身（`sk-…`）不含 `password:` 前缀，单查片段不匹配任何规则；命中隐藏在
    **原文区间**里。修复前这条会放行并改写磁盘。
    """

    root = make_secret_repo(tmp_path)
    target = root / "config" / "app.yml"
    before = target.read_bytes()

    with pytest.raises(RedactedEditError):
        apply_edits(
            root,
            [
                EditRequest(
                    path="config/app.yml",
                    old_text="sk-abcdefghijklmnopqrst",
                    replacement="已轮换",
                )
            ],
        )

    assert target.read_bytes() == before


def test_overlap_with_only_part_of_the_match_is_refused(tmp_path: Path) -> None:
    """只覆盖敏感命中区间的一段同样算相交（左半、右半各测一次）。"""

    root = make_secret_repo(tmp_path)
    target = root / "config" / "app.yml"
    before = target.read_bytes()
    for fragment in ("  token: sk-abc", "klmnopqrst"):
        with pytest.raises(RedactedEditError):
            apply_edits(
                root,
                [
                    EditRequest(
                        path="config/app.yml",
                        old_text=fragment,
                        replacement="已轮换",
                    )
                ],
            )
        assert target.read_bytes() == before


def test_adjacent_edit_still_succeeds_and_keeps_the_hidden_line(tmp_path: Path) -> None:
    root = make_secret_repo(tmp_path)
    target = root / "config" / "app.yml"

    outcome = apply_edits(
        root,
        [
            EditRequest(
                path="config/app.yml",
                line_range=LineRange(start=3, end=3),
                replacement="  retries: 5",
            )
        ],
    )

    assert outcome.outcomes[0].added_lines == 1
    text = target.read_text(encoding="utf-8")
    assert "  retries: 5" in text
    assert "  token: sk-abcdefghijklmnopqrst" in text


def test_conflict_hint_only_appears_when_the_file_really_has_hits(tmp_path: Path) -> None:
    """不要把冲突都归咎脱敏：没有命中时保持中性提示。"""

    secret_root = make_secret_repo(tmp_path / "secret")

    with pytest.raises(EditConflictError) as polluted:
        apply_edits(
            secret_root,
            [
                EditRequest(
                    path="config/app.yml",
                    old_text="  token: [REDACTED]",
                    replacement="  token: 已轮换",
                )
            ],
        )
    assert polluted.value.code == "edit_conflict"
    assert "脱敏" in str(polluted.value)

    clean_root = make_repo(tmp_path / "clean")

    with pytest.raises(EditConflictError) as clean:
        apply_edits(
            clean_root,
            [EditRequest(path="src/store.py", old_text="不存在的原文", replacement="x")],
        )
    assert clean.value.code == "edit_conflict"
    assert "脱敏" not in str(clean.value)


def test_batch_with_one_refused_edit_writes_nothing(tmp_path: Path) -> None:
    """批量补丁任一项拒绝则整批不写——干净的那一项即使排在前面也不得落盘。"""

    root = make_secret_repo(tmp_path)
    clean = root / "config" / "other.yml"
    clean.write_text("retries: 1\n", encoding="utf-8")
    secret_file = root / "config" / "app.yml"
    clean_before = clean.read_bytes()
    secret_before = secret_file.read_bytes()

    with pytest.raises(RedactedEditError):
        apply_edits(
            root,
            [
                EditRequest(
                    path="config/other.yml", old_text="retries: 1", replacement="retries: 2"
                ),
                EditRequest(
                    path="config/app.yml",
                    line_range=LineRange(start=2, end=2),
                    replacement="  token: 已轮换",
                ),
            ],
        )

    assert clean.read_bytes() == clean_before
    assert secret_file.read_bytes() == secret_before


@pytest.mark.asyncio
async def test_model_visible_edit_output_hides_the_nearby_secret(tmp_path: Path) -> None:
    """diff 带 3 行上下文，会把隐藏行带进工具输出；模型看到的那一份必须已脱敏。

    这条把 §2.3 的两半连起来：编辑门禁拦住"写回隐藏区间"，而邻近编辑的 diff
    仍然可能**读到**隐藏行，因此必须靠工具输出的安全投影兜住。
    """

    root = make_secret_repo(tmp_path)
    tool = EditFileTool(root, authorization=ProjectAuthorization.READ_WRITE)
    executor = ToolExecutor(
        ToolRegistry([tool]),
        InMemoryEventSink(uuid4()),
        timeout_seconds=5,
        max_result_chars=20_000,
    )

    result = await executor.execute(
        ToolCall(
            call_id="c1",
            name="edit_file",
            arguments={
                "path": "config/app.yml",
                "start_line": 3,
                "end_line": 3,
                "replacement": "  retries: 5",
            },
        )
    )

    assert result.status is ToolResultStatus.SUCCESS
    assert "sk-abcdefghijklmnopqrst" not in result.content
    assert result.view_metadata is not None
    assert result.view_metadata.source_view == "redacted"
    # 未涉及的隐藏行仍在磁盘上原样保留（有损视图不修改源文件）
    assert "  token: sk-abcdefghijklmnopqrst" in (root / "config" / "app.yml").read_text(
        encoding="utf-8"
    )
