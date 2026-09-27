"""整理分类规则（fixture 自带的业务逻辑）。"""

from pathlib import Path

PLACEMENT = {".md": "notes", ".txt": "notes", ".pdf": "reports"}


def classify_file(path: Path) -> str | None:
    """按后缀决定放到哪个目录；不认识的类型返回 None（不猜）。"""

    return PLACEMENT.get(path.suffix.lower())


def plan_targets(root: Path) -> list[Path]:
    return sorted(item for item in root.iterdir() if classify_file(item) is not None)
