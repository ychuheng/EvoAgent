"""检查仓库内 Markdown 文件的相对链接目标是否存在。

用法：
    python scripts/check_doc_links.py [仓库根目录]

只检查相对路径链接（跳过 http/https/mailto/纯锚点），用于文档整理或移动文件后确认
没有断链。发现断链时打印清单并以退出码 1 结束，便于接入本地检查或 CI。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from urllib.parse import unquote

LINK = re.compile(r"!?\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
SKIP_DIRS = {
    ".git",
    ".venv",
    ".pytest_cache",
    ".ruff_cache",
    "__pycache__",
    "dist",
    "node_modules",
    "output",
    "tmp",
}


def check(root: Path) -> tuple[int, list[str]]:
    broken: list[str] = []
    checked = 0
    for path in sorted(root.rglob("*.md")):
        if any(part in SKIP_DIRS for part in path.relative_to(root).parts):
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            for raw in LINK.findall(line):
                target = raw.strip()
                if not target or target.startswith(
                    ("http://", "https://", "mailto:", "#", "data:")
                ):
                    continue
                target = unquote(target.split("#", 1)[0])
                if not target:
                    continue
                checked += 1
                if not (path.parent / target).resolve().exists():
                    relative = path.relative_to(root).as_posix()
                    broken.append(f"{relative}:{number} -> {target}")
    return checked, broken


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
    checked, broken = check(root)
    print(f"检查相对链接 {checked} 条；断链 {len(broken)} 条")
    for item in broken:
        print(f"  BROKEN {item}")
    return 1 if broken else 0


if __name__ == "__main__":
    raise SystemExit(main())
