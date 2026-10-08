"""测量当前敏感规则在真实代码语料上的命中与误报基线。

为什么需要它（改造方案 §2.1 第 10 条与 S0a 的前置）：
"新旧逐字节等价"只能证明**重构没改变行为**，不能证明**行为本来是对的**。
本脚本给出后者：命中分布、自动分类与人工复核位。

原则：
- **绝不扫描 `.env*`**，也不打印完整命中内容；样例一律掩码。
- 分类是**启发式**，不是真值；报告同时给出 `unknown` 桶与人工复核位，
  不得把启发式分类当成已核实的误报率。
- 规则来自 `evoagent.privacy.redaction`，不在这里复制正则。

用法：
    python scripts/measure_redaction_false_positives.py --json output/redaction-fp-baseline.json
"""

from __future__ import annotations

import argparse
import json
import platform
import re
import sys
from collections import Counter
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from evoagent.privacy.redaction import (  # noqa: E402
    _SENSITIVE_TEXT_RULES,  # noqa: PLC2701 - 基线工具需要逐条规则归因
    POLICY_VERSION,
    detect_sensitive,
    redact_text,
)

DEFAULT_ROOTS = ("src", "tests", "scripts", "docs", "evals", "deploy", "frontend/src")
SCAN_SUFFIXES = frozenset(
    {".py", ".ts", ".tsx", ".md", ".toml", ".yml", ".yaml", ".json", ".jsonl", ".cfg", ".ini"}
)
SKIP_DIR_PARTS = frozenset(
    {".git", "node_modules", ".venv", "__pycache__", "dist", "build", ".pytest_cache", "output"}
)

#: 启发式分类标签；只用于分流人工复核，不作为真值。
LABEL_PATTERN_LITERAL = "pattern_literal"
LABEL_DECLARATION = "declaration_or_annotation"
LABEL_LITERAL_ASSIGNMENT = "literal_assignment"
LABEL_IDENTIFIER = "identifier_only"
LABEL_UNKNOWN = "unknown"

_ANNOTATION = re.compile(r"^\s*(?:[A-Za-z_][\w]*)\s*:\s*[A-Za-z_]")
_ASSIGNMENT_LITERAL = re.compile(r"[:=]\s*[\"']")


@dataclass(frozen=True)
class Hit:
    path: str
    line: int
    category: str
    rule_index: int
    label: str
    match_shape: str
    line_shape: str


def _mask(text: str) -> str:
    """压缩空白并把长串掩码，避免把命中内容原样写进报告。"""

    collapsed = re.sub(r"\s+", " ", text).strip()
    collapsed = re.sub(r"[A-Za-z0-9_\-]{12,}", lambda m: m.group(0)[:4] + "…", collapsed)
    return collapsed[:48]


def _classify(line: str) -> str:
    stripped = line.strip()
    if "\\" in stripped and ("|" in stripped or "[\\w" in stripped or "re.compile" in stripped):
        return LABEL_PATTERN_LITERAL
    if _ANNOTATION.match(line):
        return LABEL_DECLARATION
    if _ASSIGNMENT_LITERAL.search(line):
        return LABEL_LITERAL_ASSIGNMENT
    if (
        re.search(r"(?:^|[^A-Za-z0-9_])sk-[A-Za-z0-9_\-]{8,}", line)
        and ":" not in line.split("sk-")[0]
    ):
        return LABEL_IDENTIFIER
    return LABEL_UNKNOWN


def _iter_files(roots: Iterable[str]) -> Iterable[Path]:
    for raw in roots:
        base = ROOT / raw
        if not base.exists():
            continue
        for path in sorted(base.rglob("*")):
            if not path.is_file() or path.suffix not in SCAN_SUFFIXES:
                continue
            if path.name.startswith(".env"):
                continue
            if any(part in SKIP_DIR_PARTS for part in path.parts):
                continue
            # 扫描器不能扫自己的输出：报告里存着 `match_shape` 样例，再扫一遍会把
            # 上一轮的命中当成新命中，数字自我放大（实测会从 88 虚增到 259）。
            if path.name.startswith("redaction-fp-") and "reports" in path.parts:
                continue
            yield path


def scan(roots: Iterable[str]) -> tuple[list[Hit], Counter]:
    hits: list[Hit] = []
    stats: Counter = Counter()
    for path in _iter_files(roots):
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            stats["unreadable_files"] += 1
            continue
        stats["files"] += 1
        relative = str(path.relative_to(ROOT))
        for number, line in enumerate(text.splitlines(), 1):
            stats["lines"] += 1
            categories = detect_sensitive(line)
            if not categories:
                continue
            stats["hits"] += 1
            if redact_text(line) != line:
                stats["rewriting_hits"] += 1
            label = _classify(line)
            for index, (category, pattern, _) in enumerate(_SENSITIVE_TEXT_RULES):
                match = pattern.search(line)
                if match is None:
                    continue
                hits.append(
                    Hit(
                        path=relative,
                        line=number,
                        category=category,
                        rule_index=index,
                        label=label,
                        match_shape=_mask(match.group(0)),
                        line_shape=_mask(line),
                    )
                )
    return hits, stats


def _git(*arguments: str) -> str | None:
    """尽力取 git 信息；失败不阻断测量。"""

    import subprocess

    try:
        completed = subprocess.run(
            ("git", *arguments),
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout.strip()


def build_report(hits: list[Hit], stats: Counter, roots: Iterable[str]) -> dict:
    by_label = Counter(hit.label for hit in hits)
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "environment": {
            "revision": _git("rev-parse", "HEAD"),
            "branch": _git("rev-parse", "--abbrev-ref", "HEAD"),
            "worktree_clean": _git("status", "--porcelain") == "",
            "python": platform.python_version(),
            "policy_version": POLICY_VERSION,
        },
        "scope": "工作树文本扫描（不含 .env*），不是模型实际看到的工具输出",
        "roots": list(roots),
        "scan": {
            "files": stats["files"],
            "lines": stats["lines"],
            "unreadable_files": stats["unreadable_files"],
            "hits": stats["hits"],
            "rewriting_hits": stats["rewriting_hits"],
        },
        "by_category": dict(Counter(hit.category for hit in hits)),
        "by_label": dict(by_label),
        "match_shapes": dict(Counter(hit.match_shape for hit in hits).most_common(20)),
        "hits": [asdict(hit) for hit in hits],
        "method": {
            "classification": "heuristic（按行形状分流），不是标注真值",
            "labels": [
                LABEL_PATTERN_LITERAL,
                LABEL_DECLARATION,
                LABEL_LITERAL_ASSIGNMENT,
                LABEL_IDENTIFIER,
                LABEL_UNKNOWN,
            ],
            "human_review": "未做；`by_label` 与 `unknown` 桶需要人工逐条确认后才能称为误报率",
            "secrets": "不扫描 .env*；样例已掩码；fixture 中的合成假凭据会出现在命中里，属预期",
            "reproduce": "python scripts/measure_redaction_false_positives.py --json <out>",
        },
        "limits": [
            "扫的是仓库文本，不是模型实际看到的工具输出，因此不等于线上命中频率",
            "本仓库不代表用户项目；配置/日志密集的项目命中率会更高",
            "命中不等于误报：literal_assignment 也可能是真实凭据",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", default=None, help="限定扫描目录（默认若干产品目录）")
    parser.add_argument("--json", dest="json_path", default=None, help="写出 JSON 报告")
    parser.add_argument("--quiet", action="store_true", help="只打印汇总")
    arguments = parser.parse_args()

    roots = tuple(arguments.paths) if arguments.paths else DEFAULT_ROOTS
    hits, stats = scan(roots)
    report = build_report(hits, stats, roots)

    print(f"扫描：{report['scan']['files']} 个文件 / {report['scan']['lines']} 行")
    print(
        f"命中：{report['scan']['hits']} 处（其中会改写正文 {report['scan']['rewriting_hits']} 处）"
    )
    print(f"按类别：{report['by_category']}")
    print(f"按启发式分类：{report['by_label']}")
    print("最常见的命中形状：")
    for shape, count in list(report["match_shapes"].items())[:10]:
        print(f"  {count:4}  {shape!r}")
    if not arguments.quiet:
        print("\n样例（已掩码）：")
        for hit in hits[:10]:
            print(f"  {hit.path}:{hit.line}  [{hit.label}]  {hit.match_shape!r}")
    print(f"\n注意：{report['method']['human_review']}")

    if arguments.json_path:
        target = ROOT / arguments.json_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"\n报告已写入：{arguments.json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
