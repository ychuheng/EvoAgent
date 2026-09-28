"""对一个已保存的 RunTrace 做**逐结论引用核对**（离线，不发起任何模型或网络调用）。

用法：
    python scripts/check_answer_citations.py --trace output/run-trace.json
    python scripts/check_answer_citations.py --trace trace.json --json output/citations.json

退出码：机械核对通过返回 0，存在"没引用 / 只引摘要 / 来源不明"的结论返回 1——
**通过只代表引用的来源确实读过正文，不代替人工事实核对**。
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from evoagent.evals.citations import render_citation_report, report_from_trace

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trace", type=Path, required=True, help="RunTrace 的 JSON 文件")
    parser.add_argument("--json", type=Path, default=None, help="把机器可读结果写到该路径")
    parser.add_argument("--markdown", type=Path, default=None, help="把核对表写到该路径")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    trace = json.loads(args.trace.read_text(encoding="utf-8-sig"))
    report = report_from_trace(trace)
    markdown = render_citation_report(report)
    if not args.quiet:
        print(markdown)
    if args.markdown is not None:
        args.markdown.parent.mkdir(parents=True, exist_ok=True)
        args.markdown.write_text(markdown, encoding="utf-8")
        print(f"已写出核对表 {args.markdown}")
    if args.json is not None:
        payload = {
            "schema_version": 1,
            "passed": report.passed,
            "counts": report.counts,
            "note": report.note,
            "conclusions": [asdict(item) for item in report.conclusions],
        }
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"已写出机器可读结果 {args.json}")
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
