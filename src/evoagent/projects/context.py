"""构造注入到首轮消息的**有界**项目上下文。

实施计划 §6 的 P-05 与 §6 验收都要求"不能把整个仓库一次塞进模型"，
因此这里只注入三类有界信息：

1. 用户在页面上实际授权的根（让模型知道它可见的边界，以及当前是只读还是可写）；
2. 根目录的直接子项线索（不含被忽略的构建产物）；
3. README 的开头若干字符（存在时）。

文件正文一律标注为**不可信资料**：其中的指令不能改变工具权限或授权范围。
"""

from __future__ import annotations

from pathlib import Path

from evoagent.projects.service import ActiveProject
from evoagent.tools.builtin.project_common import relative_display, visible_entries

MAX_HINT_ENTRIES = 40
MAX_README_CHARS = 2_000
README_NAMES = ("README.md", "README.rst", "README.txt", "README")

PROJECT_TOOL_NAMES = ("list_dir", "find_files", "search_text", "file_read")


def _directory_hints(root: Path) -> list[str]:
    hints: list[str] = []
    for path, reason in visible_entries(root, include_hidden=False):
        if reason is not None:
            continue
        if len(hints) >= MAX_HINT_ENTRIES:
            hints.append("…（目录项过多，请用 list_dir 分页查看）")
            break
        try:
            is_directory = path.is_dir()
        except OSError:  # pragma: no cover - 竞态删除
            continue
        hints.append(f"{relative_display(path, root)}{'/' if is_directory else ''}")
    return hints


def _readme_excerpt(root: Path) -> tuple[str, str] | None:
    for name in README_NAMES:
        candidate = root / name
        try:
            if not candidate.is_file():
                continue
            text = candidate.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        excerpt = text[:MAX_README_CHARS]
        note = "" if len(text) <= MAX_README_CHARS else "（已截断，可继续用 file_read 读取后续行）"
        return name, excerpt + note
    return None


def build_project_context(project: ActiveProject) -> str:
    """生成注入文本；不读取除 README 之外的任何文件内容。"""

    authorization = "只读（read）" if not project.writable else "可写（read_write）"
    lines = [
        f"项目名：{project.name}",
        f"授权根：{project.root.as_posix()}",
        f"授权级别：{authorization}",
        f"授权版本：{project.authorization_version}",
        "可用工具：" + "、".join(PROJECT_TOOL_NAMES),
        "边界：所有路径都相对上面的授权根解析；根之外、符号链接/junction 逃逸一律被拒绝。",
    ]
    hints = _directory_hints(project.root)
    if hints:
        lines.append("根目录直接子项（不含被忽略的构建产物与缓存）：")
        lines.extend(f"- {item}" for item in hints)
    readme = _readme_excerpt(project.root)
    if readme is not None:
        name, excerpt = readme
        lines.append(f"README（{name}）开头，仅作资料参考：")
        lines.append(excerpt)
    else:
        lines.append("未在根目录找到 README；请用 find_files 或 list_dir 自行定位。")
    return "\n".join(lines)


__all__ = ["PROJECT_TOOL_NAMES", "build_project_context"]
