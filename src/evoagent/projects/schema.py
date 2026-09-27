"""授权项目的领域模型：根路径规范化、授权级别与授权状态。

M1 的核心约束（实施计划 §6）：

- 用户显式登记一个**物理根目录**，Agent 只能在该根内活动；模型不能自行登记任意宿主路径。
- 授权分 `read`（默认）与 `read_write`（须显式选择）两级；登记时即冻结根路径。
- 撤销授权后**新的** Task 不得再进入旧根；运行中的 Task 在下一次工具调用时被拒绝。
- 根目录规范化后必须仍指向同一物理位置，否则拒绝登记（避免 symlink/junction 逃逸与根漂移）。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from evoagent.tools.base import ToolExecutionError, ToolPermissionError

# 根路径长度上限与 SQLAlchemy 列宽保持一致。
MAX_ROOT_LENGTH = 2_048


class ProjectAuthorization(StrEnum):
    """项目授权级别。"""

    READ = "read"
    READ_WRITE = "read_write"


class ProjectStatus(StrEnum):
    """项目可用状态。"""

    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    REVOKED = "revoked"


class ProjectRegistrationError(Exception):
    """用户可以看懂的登记失败原因。"""

    code = "project_registration_error"


class ProjectNotFoundError(Exception):
    code = "project_not_found"


class ProjectAuthorizationError(Exception):
    """授权不足或已被撤销。"""

    code = "project_authorization_denied"


class ProjectAuthorizationRevoked(ProjectAuthorizationError):
    """运行中授权发生变化：本次调用被拒绝，调用方必须给出明确终态。

    与普通拒绝的区别在于它**必须终止运行**，而不是让模型继续换一种做法。
    """

    code = "authorization_revoked"

    def __init__(self, message: str, *, project_id: str, detail: str) -> None:
        super().__init__(message)
        self.project_id = project_id
        self.detail = detail


@dataclass(frozen=True, slots=True)
class NormalizedRoot:
    """登记时冻结的物理根。"""

    path: Path
    display: str


def normalize_root(raw: str) -> NormalizedRoot:
    """把用户输入规范化成稳定的物理根路径。

    拒绝：空值、相对路径（避免"相对谁"的歧义）、不存在的目录、不是目录的目标、
    以及解析后与输入指向不同物理位置的情况（symlink/junction）。
    """

    text = raw.strip().strip('"').strip("'")
    if not text:
        raise ProjectRegistrationError("项目根路径不能为空")
    if len(text) > MAX_ROOT_LENGTH:
        raise ProjectRegistrationError(f"项目根路径过长（上限 {MAX_ROOT_LENGTH} 字符）")

    candidate = Path(text).expanduser()
    if not candidate.is_absolute():
        raise ProjectRegistrationError("项目根必须是绝对路径，避免相对路径歧义")

    try:
        # strict=True：不存在的目录直接拒绝，不创建。
        resolved = candidate.resolve(strict=True)
    except (OSError, ValueError) as error:
        raise ProjectRegistrationError(f"项目根不可用：{error}") from error
    if not resolved.is_dir():
        raise ProjectRegistrationError("项目根必须是一个目录")

    # 输入本身若是链接，解析后会指向别处；这会让"用户看到的根"与"Agent 实际可见的根"不一致。
    lexically_same = os.path.normcase(os.path.normpath(str(candidate))) == os.path.normcase(
        str(resolved)
    )
    if not lexically_same:
        raise ProjectRegistrationError(
            "项目根是符号链接或 junction；请登记它指向的真实目录，避免授权根与可见根不一致"
        )

    return NormalizedRoot(path=resolved, display=resolved.as_posix())


def ensure_candidate_inside_root(root: Path, candidate: Path) -> Path:
    """确认候选路径（可能尚不存在）的**词法**解析结果位于根内。

    只做词法检查，不跟随链接；调用方随后必须再对已存在的目标做物理检查。
    """

    normalized_root = os.path.normcase(os.path.normpath(str(root)))
    normalized_candidate = os.path.normcase(os.path.normpath(str(candidate)))
    if normalized_candidate == normalized_root:
        return candidate
    if not normalized_candidate.startswith(normalized_root + os.sep):
        raise ToolPermissionError("路径超出已授权项目根")
    return candidate


def resolve_inside_root(root: Path, requested: str) -> tuple[Path, Path]:
    """把请求路径解析成 (词法目标, 物理目标)，两级都必须在根内。

    与 `WorkspaceGuard` 的区别：这里额外拒绝**任何**中间环节的符号链接/junction 逃逸，
    并返回词法目标供调用方在目标不存在时使用（`find_files`/`search_text` 需要遍历）。
    """

    if not requested.strip():
        raise ToolExecutionError("路径不能为空")
    if "\x00" in requested:
        raise ToolExecutionError("路径包含非法字符")
    try:
        raw = Path(requested).expanduser()
    except (OSError, ValueError) as error:
        raise ToolExecutionError("路径无效") from error

    root = root.resolve(strict=False)
    lexically = raw if raw.is_absolute() else root / raw
    try:
        lexical_target = Path(os.path.normpath(str(lexically)))
    except (OSError, ValueError) as error:
        raise ToolExecutionError("路径无效") from error
    ensure_candidate_inside_root(root, lexical_target)

    if not lexical_target.exists():
        # 目标不存在：调用方按"目录可遍历、文件不可读"处理。
        return lexical_target, lexical_target

    try:
        physical = lexical_target.resolve(strict=True)
    except (OSError, ValueError) as error:
        raise ToolExecutionError("路径不存在或无法解析") from error
    _ensure_physical_inside(root, physical)
    _reject_link_escape(root, lexical_target, physical)
    return lexical_target, physical


def _ensure_physical_inside(root: Path, physical: Path) -> None:
    normalized_root = os.path.normcase(os.path.normpath(str(root)))
    normalized_physical = os.path.normcase(os.path.normpath(str(physical)))
    if normalized_physical != normalized_root and not normalized_physical.startswith(
        normalized_root + os.sep
    ):
        raise ToolPermissionError("路径超出已授权项目根")


def _reject_link_escape(root: Path, lexical: Path, physical: Path) -> None:
    """词法上在根内、物理上却跳到根外，说明中间有链接，直接拒绝。

    用 `samefile` 比较（而不是字符串比较）以兼容大小写不敏感的文件系统：
    没有链接时两者必然指向同一个文件。
    """

    try:
        lexical.relative_to(root)
    except ValueError as error:  # pragma: no cover - ensure_candidate_inside_root 已拦截
        raise ToolPermissionError("路径超出已授权项目根") from error
    try:
        if os.path.samefile(lexical, physical):
            return
    except OSError:  # pragma: no cover - 两侧都已确认存在
        return
    raise ToolPermissionError("路径经过符号链接或 junction 跳出了已授权项目根")
