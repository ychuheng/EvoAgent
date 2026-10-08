"""逐文件编辑：哈希前置条件、冲突检测、原子替换（实施计划 §7 E-01/E-03）。

契约要点：

- **哈希前置条件**：`expected_sha256` 为空时允许对已存在文件编辑，但必须给出
  `old_text` 或行范围；不匹配即报冲突，绝不"尽力而为"地改写。
- **冲突检测**：应用前重新读盘并复核哈希与定位文本；期间被改动就抛 `EditConflictError`。
- **原子替换**：先在同目录写临时文件（保证同一分区），再 `os.replace` 覆盖；
  权限位与编码/BOM/行尾按原文件还原。
- **多文件**：先全部做前置条件校验，再逐个替换；中途失败用已写文件的原始字节回滚。
"""

from __future__ import annotations

import difflib
import hashlib
import os
import tempfile
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path

from evoagent.privacy.redaction import detect_sensitive
from evoagent.projects.encoding import DetectedText, count_line_endings, read_text
from evoagent.projects.schema import resolve_inside_root
from evoagent.tools.base import ToolExecutionError, ToolPermissionError

MAX_EDIT_BYTES = 1_000_000
MAX_DIFF_CHARS = 20_000


class EditConflictError(ToolExecutionError):
    """文件在读取之后被改动，或定位文本不唯一/不存在。"""

    code = "edit_conflict"


class RedactedEditError(ToolExecutionError):
    """待替换的区间命中当前敏感规则：模型多半在照有损视图写回。"""

    code = "redacted_edit_requires_review"


@dataclass(frozen=True, slots=True)
class LineRange:
    """用 1 起始的闭区间行范围定位要替换的内容。"""

    start: int
    end: int

    def __post_init__(self) -> None:
        if self.start < 1 or self.end < self.start:
            raise ValueError("行范围无效")


@dataclass(frozen=True, slots=True)
class EditRequest:
    """一次单文件编辑。"""

    path: str
    replacement: str
    expected_sha256: str | None = None
    old_text: str | None = None
    line_range: LineRange | None = None
    create: bool = False

    def __post_init__(self) -> None:
        locators = [self.old_text is not None, self.line_range is not None]
        if sum(locators) > 1:
            raise ValueError("old_text 与 line_range 只能给出一个")
        if not self.create and not any(locators):
            raise ValueError("编辑已存在的文件必须给出 old_text 或 line_range")


@dataclass(slots=True)
class FileSnapshot:
    """应用编辑前的文件状态，用于回滚与 diff。"""

    path: Path
    display: str
    existed: bool
    raw: bytes
    detected: DetectedText | None
    mode: int | None


@dataclass(slots=True)
class EditOutcome:
    display: str
    created: bool
    diff: str
    diff_truncated: bool
    added_lines: int
    removed_lines: int
    new_sha256: str
    line_endings_before: dict[str, int]
    line_endings_after: dict[str, int]


@dataclass(slots=True)
class PatchOutcome:
    outcomes: list[EditOutcome] = field(default_factory=list)
    rolled_back: list[str] = field(default_factory=list)


def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _display(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:  # pragma: no cover - 调用方已保证在根内
        return path.name


def _target_file(root: Path, requested: str, *, create: bool) -> tuple[Path, str]:
    lexical, physical = resolve_inside_root(root, requested)
    if physical.is_dir():
        raise ToolExecutionError("拒绝删除目录；请先逐个列出要删除的文件")
    if physical.is_symlink():
        raise ToolPermissionError("拒绝通过符号链接编辑文件")
    if physical.exists():
        if not physical.is_file():
            raise ToolExecutionError("目标不是一个普通文件")
        return physical, _display(physical, root)
    if not create:
        raise ToolExecutionError("目标文件不存在；如需新建请显式声明 create")
    parent = lexical.parent
    if not parent.is_dir():
        raise ToolExecutionError("目标目录不存在，请先确认路径")
    return lexical, _display(lexical, root)


def _locate(text: str, request: EditRequest, *, newline: str) -> tuple[int, int, str]:
    """返回替换区间的字符偏移与原内容。"""

    if request.old_text is not None:
        needle = request.old_text
        if not needle:
            raise ToolExecutionError("old_text 不能为空；请给出要替换的原文")
        # 行尾不同时，模型给的多半是 `\n`；按文件的主行尾再试一次。
        candidates = [needle]
        normalized = normalize_line_endings(needle, newline)
        if normalized != needle:
            candidates.append(normalized)
        for candidate in candidates:
            occurrences = text.count(candidate)
            if occurrences == 1:
                start = text.index(candidate)
                return start, start + len(candidate), candidate
            if occurrences > 1:
                raise EditConflictError(
                    f"old_text 在文件中出现 {occurrences} 次，无法唯一定位；"
                    "请给出更长的上下文或改用 line_range"
                )
        raise EditConflictError(
            "old_text 在文件中不存在；文件可能已被他人修改" + _redaction_hint(text)
        )

    assert request.line_range is not None
    lines = text.splitlines(keepends=True)
    if request.line_range.end > len(lines):
        raise EditConflictError(
            f"行范围 {request.line_range.start}-{request.line_range.end} "
            f"超出文件总行数 {len(lines)}"
        )
    start = sum(len(item) for item in lines[: request.line_range.start - 1])
    end = start + sum(
        len(item) for item in lines[request.line_range.start - 1 : request.line_range.end]
    )
    return start, end, text[start:end]


def normalize_line_endings(text: str, newline: str) -> str:
    """把文本里的任何行尾统一成目标行尾（模型给的多半是 `\\n`）。"""

    if newline == "\n":
        return text
    return text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", newline)


def _redaction_hint(text: str) -> str:
    """只在文件当前**确有**敏感命中时提示可能是脱敏视图。

    "不要把冲突都归咎脱敏"：没有命中就保持原来的中性提示，否则模型会误判原因。
    """

    hits = detect_sensitive(text)
    if not hits:
        return ""
    return (
        f"；注意该文件当前确有敏感片段命中（{'、'.join(hits)}），"
        "你可能读到的是脱敏视图，请不要反复换参数重试"
    )


def check_sensitive_edit(snapshot: FileSnapshot, request: EditRequest, located_span: str) -> None:
    """写盘前用当前规则检查**实际被替换的区间**（改造方案 §2.3）。

    为什么不能只靠 old_text 失败：`line_range` 直接定位真实行，根本不需要模型持有
    原文；`expected_sha256` 匹配也只说明文件没被改过，不证明模型见过完整内容。
    因此这里检查真正会被覆盖的那段磁盘文本——命中即拒绝，不靠模型自觉。
    """

    hits = detect_sensitive(located_span)
    if not hits:
        return
    raise RedactedEditError(
        f"{snapshot.display} 中待替换的区间命中当前敏感规则（{'、'.join(hits)}）；"
        "该区间在模型视图里是隐藏的，写回会破坏原文。"
        "请停止该编辑并请用户本地处理，或先纠正规则误报后重新读取。"
    )


def _apply_to_text(text: str, request: EditRequest, *, newline: str) -> tuple[str, str, str]:
    """返回 `(原内容, 新内容, 被替换区间的原文)`。"""

    if request.create and text == "" and request.old_text is None and request.line_range is None:
        return "", normalize_line_endings(request.replacement, newline), ""
    start, end, original = _locate(text, request, newline=newline)
    replacement = normalize_line_endings(request.replacement, newline)
    return original, f"{text[:start]}{replacement}{text[end:]}", original


def _atomic_write(path: Path, raw: bytes, *, mode: int | None) -> None:
    """同目录临时文件 + `os.replace`，保证读者看到的要么是旧内容要么是新内容。"""

    directory = path.parent
    handle, temporary = tempfile.mkstemp(prefix=".evoagent-", dir=directory)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        if mode is not None:
            os.chmod(temporary, mode)
        os.replace(temporary, path)
    except OSError as error:
        raise ToolExecutionError(f"写入 {path.name} 失败：{error}") from error
    finally:
        if os.path.exists(temporary):
            with suppress(OSError):  # 清理失败不影响已完成的替换
                os.unlink(temporary)


def _snapshot(path: Path, *, display: str) -> FileSnapshot:
    if not path.exists():
        return FileSnapshot(
            path=path, display=display, existed=False, raw=b"", detected=None, mode=None
        )
    try:
        raw = path.read_bytes()
        mode = path.stat().st_mode
    except OSError as error:
        raise ToolExecutionError(f"{display} 无法读取") from error
    if len(raw) > MAX_EDIT_BYTES:
        raise ToolExecutionError(f"{display} 超过 {MAX_EDIT_BYTES} 字节的编辑上限")
    return FileSnapshot(
        path=path,
        display=display,
        existed=True,
        raw=raw,
        detected=read_text(path, display=display),
        mode=mode,
    )


def _check_precondition(snapshot: FileSnapshot, request: EditRequest) -> None:
    if request.create:
        if snapshot.existed:
            raise EditConflictError(f"{snapshot.display} 已存在，不能按新建处理")
        return
    if not snapshot.existed:
        raise EditConflictError(f"{snapshot.display} 不存在，无法按修改处理")
    if request.expected_sha256:
        actual = sha256_bytes(snapshot.raw)
        if actual != request.expected_sha256:
            raise EditConflictError(
                f"{snapshot.display} 的内容已变化（期望 {request.expected_sha256[:12]}…，"
                f"实际 {actual[:12]}…）；请重新读取后再改"
            )


def _make_diff(before: list[str], after: list[str], display: str) -> tuple[str, bool]:
    diff = "".join(
        difflib.unified_diff(before, after, fromfile=f"a/{display}", tofile=f"b/{display}", n=3)
    )
    if len(diff) > MAX_DIFF_CHARS:
        return diff[:MAX_DIFF_CHARS] + "\n…（diff 已截断）", True
    return diff, False


def _count_changes(diff: str) -> tuple[int, int]:
    """从 unified diff 统计新增与删除行数，忽略 `+++`/`---` 头。"""

    added = sum(
        1 for line in diff.splitlines() if line.startswith("+") and not line.startswith("+++")
    )
    removed = sum(
        1 for line in diff.splitlines() if line.startswith("-") and not line.startswith("---")
    )
    return added, removed


def apply_edits(
    root: Path,
    requests: list[EditRequest],
    *,
    dry_run: bool = False,
) -> PatchOutcome:
    """应用一批编辑；全部前置条件通过后才写盘，任一步失败即回滚已写文件。"""

    if not requests:
        raise ToolExecutionError("至少需要一条编辑")
    seen: set[str] = set()
    snapshots: list[FileSnapshot] = []
    prepared: list[tuple[FileSnapshot, EditRequest, bytes, EditOutcome]] = []

    for request in requests:
        path, display = _target_file(root, request.path, create=request.create)
        if display in seen:
            raise ToolExecutionError(f"{display} 在同一批编辑里出现多次")
        seen.add(display)
        snapshot = _snapshot(path, display=display)
        _check_precondition(snapshot, request)

        if snapshot.detected is None:
            text = ""
            newline = "\n"
        else:
            text = snapshot.detected.text
            newline = snapshot.detected.newline
        original, updated, located = _apply_to_text(text, request, newline=newline)
        # 写盘前检查真正会被覆盖的区间；这一步在写入循环之前，因此批量补丁里
        # 任一项被拒绝时整批都不会写（改造方案 §2.3）。
        check_sensitive_edit(snapshot, request, located)
        if original == request.replacement and request.old_text is not None:
            raise ToolExecutionError(f"{display} 的替换内容与原内容相同，没有可应用的改动")

        if snapshot.detected is None:
            encoded = updated.encode("utf-8")
        else:
            encoded = snapshot.detected.encode_like(updated)
        if len(encoded) > MAX_EDIT_BYTES:
            raise ToolExecutionError(f"{display} 的结果超过 {MAX_EDIT_BYTES} 字节的编辑上限")

        before_lines = text.splitlines(keepends=True)
        after_lines = updated.splitlines(keepends=True)
        diff, truncated = _make_diff(before_lines, after_lines, display)
        added, removed = _count_changes(diff)
        outcome = EditOutcome(
            display=display,
            created=not snapshot.existed,
            diff=diff,
            diff_truncated=truncated,
            added_lines=added,
            removed_lines=removed,
            new_sha256=sha256_bytes(encoded),
            line_endings_before=count_line_endings(text),
            line_endings_after=count_line_endings(updated),
        )
        snapshots.append(snapshot)
        prepared.append((snapshot, request, encoded, outcome))

    result = PatchOutcome()
    if dry_run:
        result.outcomes = [item[3] for item in prepared]
        return result

    written: list[FileSnapshot] = []
    try:
        for snapshot, _request, encoded, outcome in prepared:
            # `_atomic_write` 用同目录临时文件 + os.replace：读者要么看到旧内容要么看到新内容。
            # 从校验到写盘之间的窗口靠 expected_sha256 前置条件约束，不靠"尽力而为"。
            _atomic_write(snapshot.path, encoded, mode=snapshot.mode)
            written.append(snapshot)
            result.outcomes.append(outcome)
    except Exception:
        for snapshot in reversed(written):
            try:
                if snapshot.existed:
                    _atomic_write(snapshot.path, snapshot.raw, mode=snapshot.mode)
                elif snapshot.path.exists():
                    snapshot.path.unlink()
                result.rolled_back.append(snapshot.display)
            except (OSError, ToolExecutionError):  # pragma: no cover - 回滚尽力而为
                pass
        raise
    return result


def move_path(root: Path, source: str, destination: str) -> tuple[str, str]:
    """同根内重命名/移动；目标必须不存在。"""

    source_path, source_display = _target_file(root, source, create=False)
    target_path, target_display = _target_file(root, destination, create=True)
    if target_path.exists():
        raise EditConflictError(f"目标 {target_display} 已存在，拒绝覆盖")
    try:
        os.replace(source_path, target_path)
    except OSError as error:
        raise ToolExecutionError(f"重命名失败：{error}") from error
    return source_display, target_display


def delete_path(root: Path, requested: str) -> str:
    """删除单个文件；目录删除必须显式使用目录工具，避免隐式递归清空。"""

    path, display = _target_file(root, requested, create=False)
    if path.is_dir():
        raise ToolExecutionError("拒绝删除目录；请先逐个列出要删除的文件")
    try:
        path.unlink()
    except OSError as error:
        raise ToolExecutionError(f"删除失败：{error}") from error
    return display
