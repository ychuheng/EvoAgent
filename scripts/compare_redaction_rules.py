"""固定文件集合、只换规则，量化一次规则扩容的真实影响。

为什么需要：误报基线的总数变化混了**两件事**——规则变宽，以及工作树本身长大。
只看"扩容前 88 → 扩容后 115"会把树增长算到规则头上。本脚本对**同一批文件**分别跑
旧实现（冻结 oracle）与当前实现，给出可归因的差值，并列出"只有新规则命中"的位置。

用法：
    python scripts/compare_redaction_rules.py
    python scripts/compare_redaction_rules.py --show 30

判读方式：新增命中若集中在 `tests/fixtures/`（合成样本）就是预期；出现在 `src/` 或
`docs/` 才是需要人工确认的误报。脚本只打印**截断后的行首**，不打印完整命中内容。
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:  # 直接运行时仓库根不在 sys.path 上
    sys.path.insert(0, str(ROOT))

from evoagent.privacy import redaction  # noqa: E402
from scripts.measure_redaction_false_positives import SCAN_SUFFIXES, _iter_files  # noqa: E402

ORACLE_PATH = ROOT / "tests" / "fixtures" / "redaction" / "oracle.py"
DEFAULT_ROOTS = ("src", "tests", "scripts", "migrations", "docs", "deploy", "evals", "frontend")


def _load_oracle():
    spec = importlib.util.spec_from_file_location("redaction_oracle", ORACLE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--show", type=int, default=12, help="最多列出多少个文件的新增命中")
    arguments = parser.parse_args()

    oracle = _load_oracle()
    files = lines = old_hits = new_hits = 0
    only_new: dict[str, list[tuple[int, str]]] = {}
    for path in _iter_files(DEFAULT_ROOTS):
        if path.suffix not in SCAN_SUFFIXES:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        files += 1
        for number, line in enumerate(text.splitlines(), 1):
            lines += 1
            old = oracle.redact(line) != line
            new = redaction.redact_text(line) != line
            old_hits += old
            new_hits += new
            if new and not old:
                only_new.setdefault(path.as_posix(), []).append((number, line.strip()[:60]))

    print(f"文件 {files}、行 {lines}")
    print(
        f"旧规则命中 {old_hits}；新规则命中 {new_hits}；纯扩容新增 {new_hits - old_hits}"
        f"（当前 POLICY_VERSION={redaction.POLICY_VERSION}）"
    )
    print("新增命中的文件分布（按数量降序）：")
    for name, items in sorted(only_new.items(), key=lambda kv: -len(kv[1]))[: arguments.show]:
        print(f"  {name}: {len(items)}")
        for number, sample in items[:2]:
            print(f"     L{number}  {sample}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
