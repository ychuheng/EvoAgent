"""M6 S-04 人工发布 / 禁用 / 回滚审查的命令行入口。

计划 §11 S-04 要求"审查来源、diff、评测与安全影响，发布版本并监测；效果退化可禁用/回滚"，
完成判据是"用户能解释它学到了什么、帮了什么、失败在哪里"。本脚本生成审查记录模板、
校验填写结果、并渲染成给人读的审查页。

**它不调用 `SkillService`**：发布/禁用/回滚仍由既有产品入口执行，本脚本只保证
"人工审查这一层"有记录、有证据绑定、结论不强于证据。

用法：
    python scripts/m6_skill_review.py template --skill-id demo --version-id <uuid> \\
        --action publish --out tmp/review.json
    python scripts/m6_skill_review.py scan --record tmp/review.json
    python scripts/m6_skill_review.py validate --record tmp/review.json
    python scripts/m6_skill_review.py render --record tmp/review.json --out out.md
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from evoagent.skills.review import (  # noqa: E402
    ACTIONS,
    DIMENSIONS,
    ReviewRecord,
    assert_review_allows,
    record_from_dict,
    render_review,
    review_passed,
    scan_skill_text,
    validate_review,
)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")

TEMPLATE = {
    "reviewer": "",
    "reviewer_role": "implementer_self_review",
    "learned": "",
    "helped": "",
    "failed": "",
    "claim_scope": "no_effect_claim",
    "dimensions": {name: [] for name in DIMENSIONS},
    "evaluation": {
        "experiment_id": "",
        "verdict": "",
        "gate_passed": False,
        "gate_report_hash": "",
        "dataset_sha256": "",
        "report_path": "",
    },
    "risk_level": "",
    "approval_points": [],
    "unmitigated_risks": [],
    "acknowledged_findings": [],
    "monitoring_plan": "",
    "rollback_plan": "",
    "reason": "",
}


def load_record(path: Path) -> ReviewRecord:
    return record_from_dict(json.loads(path.read_text(encoding="utf-8-sig")))


def command_template(args: argparse.Namespace) -> int:
    text = ""
    if args.skill_text is not None:
        text = args.skill_text.read_text(encoding="utf-8")
    payload = json.loads(json.dumps(TEMPLATE))
    payload["subject"] = {
        "skill_id": args.skill_id,
        "skill_name": args.skill_name or args.skill_id,
        "version_id": args.version_id,
        "action": args.action,
        "skill_text": text,
        "from_version_id": args.from_version,
        "target_version_status": args.target_status,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"已写出审查模板 {args.out}")
    findings = scan_skill_text(text)
    if findings:
        print(
            f"安全扫描预检：{len(findings)} 项发现"
            "（填写 record 时用 acknowledged_findings 逐条处置）"
        )
        for item in findings:
            print(f"  - [{item.code}] {item.detail}")
    else:
        print("安全扫描预检：无发现")
    return 0


def command_scan(args: argparse.Namespace) -> int:
    record = load_record(args.record)
    findings = scan_skill_text(record.subject.skill_text)
    print(f"安全扫描：{len(findings)} 项发现")
    for item in findings:
        handled = "已处置" if item.code in record.acknowledged_findings else "未处置"
        print(f"  - [{item.code}] {handled}：{item.detail}")
    return 0 if all(item.code in record.acknowledged_findings for item in findings) else 1


def command_validate(args: argparse.Namespace) -> int:
    record = load_record(args.record)
    checks = validate_review(record)
    for item in checks:
        print(f"[{'通过' if item.passed else '未通过'}] {item.name}: {item.detail}")
    if review_passed(checks):
        print(f"人工审查通过：{record.subject.action} 可以交给产品入口执行")
        return 0
    failed = sum(1 for item in checks if not item.passed)
    print(f"人工审查未通过：{failed} 项")
    if args.strict:
        assert_review_allows(record)
    return 1


def command_render(args: argparse.Namespace) -> int:
    record = load_record(args.record)
    markdown = render_review(record)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(markdown, encoding="utf-8")
    print(f"已写出审查页 {args.out}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    template = sub.add_parser("template", help="生成审查记录模板")
    template.add_argument("--skill-id", required=True)
    template.add_argument("--skill-name", default=None)
    template.add_argument("--version-id", required=True)
    template.add_argument("--action", choices=ACTIONS, default="publish")
    template.add_argument("--skill-text", type=Path, default=None)
    template.add_argument("--from-version", default=None)
    template.add_argument("--target-status", default=None)
    template.add_argument("--out", type=Path, required=True)
    template.set_defaults(func=command_template)

    scan = sub.add_parser("scan", help="只跑安全扫描")
    scan.add_argument("--record", type=Path, required=True)
    scan.set_defaults(func=command_scan)

    validate = sub.add_parser("validate", help="校验审查记录")
    validate.add_argument("--record", type=Path, required=True)
    validate.add_argument("--strict", action="store_true", help="未通过时抛出异常（供脚本调用）")
    validate.set_defaults(func=command_validate)

    render = sub.add_parser("render", help="渲染给人读的审查页")
    render.add_argument("--record", type=Path, required=True)
    render.add_argument("--out", type=Path, required=True)
    render.set_defaults(func=command_render)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
