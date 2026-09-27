"""M5 fixture：`organize-lab` —— 一个"待整理的收件箱"专用仓库。

生成内容（脚本是唯一来源，仓库里的文件由 `--write` 落盘）：

- `inbox/`：两个 Markdown、一个文本型 PDF、一个"重名冲突"用的 Markdown，
  以及两个故意坏掉的文件（损坏 PDF、只有图片层的 PDF）与一个 GB18030 文本；
- `notes/`、`reports/`：目标目录，`reports/taken.pdf` 用来验证"目标已存在不覆盖"。

用法：
    python scripts/build_m5_fixture.py --check    # 只核对仓库内容与脚本是否一致
    python scripts/build_m5_fixture.py --write    # 首次落地或同步
"""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

from evoagent.projects.pdf_fixture import image_only_pdf, text_pdf

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "evals/fixtures/organize-lab"

README = """# Organize Lab（M5 整理 fixture）

供"授权目录整理"验收使用的小型仓库：`inbox/` 里混着 Markdown、PDF、坏文件与非 UTF-8 文本，
`notes/` 与 `reports/` 是整理目标，`reports/taken.pdf` 用来验证"目标已存在时不覆盖"。

整理规则示例：`inbox/*.md → notes/`、`inbox/*.pdf → reports/`。

```powershell
$env:PYTHONPATH = "src"
python -m unittest discover -s tests
```
"""

TEST = '''"""fixture 自检：目录结构与"坏文件"都必须在预期位置。"""

import tempfile
import unittest
from pathlib import Path

from organize_lab.check import classify_file, plan_targets


class OrganizeLabTests(unittest.TestCase):
    def test_classify_by_suffix(self) -> None:
        self.assertEqual(classify_file(Path("a.md")), "notes")
        self.assertEqual(classify_file(Path("a.pdf")), "reports")
        self.assertIsNone(classify_file(Path("a.bin")))

    def test_plan_skips_unknown_suffix(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "a.md").write_text("x", encoding="utf-8")
            (root / "b.bin").write_bytes(b"\\x00\\x01")
            plan = plan_targets(root)
            self.assertEqual([item.name for item in plan], ["a.md"])
'''

CHECK = '''"""整理分类规则（fixture 自带的业务逻辑）。"""

from pathlib import Path

PLACEMENT = {".md": "notes", ".txt": "notes", ".pdf": "reports"}


def classify_file(path: Path) -> str | None:
    """按后缀决定放到哪个目录；不认识的类型返回 None（不猜）。"""

    return PLACEMENT.get(path.suffix.lower())


def plan_targets(root: Path) -> list[Path]:
    return sorted(item for item in root.iterdir() if classify_file(item) is not None)
'''


def build_files() -> dict[str, bytes]:
    return {
        "README.md": README.encode(),
        "src/organize_lab/__init__.py": b'"""Organize lab package."""\n',
        "src/organize_lab/check.py": CHECK.encode(),
        "tests/test_organize_lab.py": TEST.encode(),
        # 待整理内容。
        "inbox/alpha.md": "# Alpha\n\n第一份笔记。\n".encode(),
        "inbox/beta.md": "# Beta\n\n第二份笔记。\n".encode(),
        "inbox/dup.md": "# Dup\n\n与 alpha 同名目标，用来触发冲突。\n".encode(),
        "inbox/summary.pdf": text_pdf("Quarterly summary", "Revenue grew by 12 percent"),
        # 刻意坏掉的文件：损坏 PDF、只有图片层的 PDF、需要显式编码声明的 GB18030 文本。
        "inbox/corrupt.pdf": b"%PDF-1.4\nthis body is not a valid pdf\n%%EOF\n",
        "inbox/scanned.pdf": image_only_pdf(),
        "inbox/legacy.txt": "国标编码的历史资料，需要显式声明编码。".encode("gb18030"),
        # 目标目录与"已占用"的目标名。
        "notes/.keep": b"",
        "reports/taken.pdf": text_pdf("Already here"),
    }


def digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="把 fixture 写到仓库")
    parser.add_argument("--check", action="store_true", help="只核对仓库内容与脚本一致")
    args = parser.parse_args()

    files = build_files()
    if args.write:
        for relative, content in files.items():
            path = FIXTURE / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        print(f"已写出 {len(files)} 个文件到 {FIXTURE.relative_to(ROOT).as_posix()}")
        return 0

    mismatched: list[str] = []
    for relative, content in files.items():
        path = FIXTURE / relative
        if not path.is_file() or digest(path.read_bytes()) != digest(content):
            mismatched.append(relative)
    if mismatched:
        print("fixture 与脚本不一致（请运行 --write 同步）：")
        for item in mismatched:
            print(f"  - {item}")
        return 1
    print(f"fixture 与脚本一致；共 {len(files)} 个文件")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
