"""M6 S-03 冻结对照实验的命令行入口：冻结检查、生成配对计划、判定结果、登记正式使用。

计划 §11 S-03 要求同模型、同工具、同预算、随机化两臂顺序，并按预注册样本量判定；
本脚本把这条链路做成可执行的检查，**不运行模型**：真实运行必须由真实模型执行器产出
带 `source="real_model"` 的结果文件，否则判定一律是"不可声称收益"。

用法：
    python scripts/m6_pair_experiment.py check --spec evals/datasets/m6-experiment-001.json
    python scripts/m6_pair_experiment.py plan --spec ... [--output output/m6-plan.json]
    python scripts/m6_pair_experiment.py analyze --spec ... --results results.json
    python scripts/m6_pair_experiment.py record --spec ... --results results.json \\
        --confirm-formal-run

`check`/`plan`/`record` 都先跑一遍 M0b 台账检查（`check_sample_ledger.py`），
确保评测对象不是已被使用或已曝光的样本。
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

# 中文 Windows 控制台常见 GBK 代码页；遇到无法编码的字符时替换而不是中途崩掉整个报告。
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")

import check_sample_ledger  # noqa: E402  (同目录脚本，复用台账检查)

from evoagent.skills.experiment import (  # noqa: E402
    ARM_CONTROL,
    ARM_TREATMENT,
    MOCK,
    REAL_MODEL,
    ArmOutcome,
    PairOutcome,
    analyze_pairs,
    assert_frozen,
    build_ledger_row,
    freeze_passed,
    render_analysis,
    validate_freeze,
)

DEFAULT_SPEC = ROOT / "evals/datasets/m6-experiment-001.json"
DATASETS = ROOT / "evals/datasets"
MANIFEST = DATASETS / "manifest.json"
RUN_LEDGER = DATASETS / "run_ledger.jsonl"


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def ledger_rows() -> list[dict]:
    if not RUN_LEDGER.is_file():
        return []
    rows: list[dict] = []
    for line in RUN_LEDGER.read_text(encoding="utf-8-sig").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def audit_ledger() -> int:
    """复用 M0b 台账检查；任何错误都让 M6 实验路径停下来。"""

    return check_sample_ledger.run_check(source_skill=None, source_run=None)


def load_outcomes(document: dict, spec: dict) -> list[PairOutcome]:
    """读取真实运行结果；缺字段或来源不明一律拒绝，不做任何默认填充。"""

    if document.get("schema_version") != 1:
        raise ValueError("结果文件的 schema_version 必须是 1")
    if document.get("experiment_id") != spec["experiment_id"]:
        raise ValueError("结果文件的 experiment_id 与预注册不一致")
    outcomes: list[PairOutcome] = []
    for index, item in enumerate(document.get("outcomes", [])):
        where = f"outcomes[{index}]"
        order = item.get("order")
        if not isinstance(order, list) or sorted(order) != sorted([ARM_CONTROL, ARM_TREATMENT]):
            raise ValueError(f"{where}: order 必须恰好包含两条臂各一次")
        raw_arms = item.get("arms")
        if not isinstance(raw_arms, dict) or set(raw_arms) != {ARM_CONTROL, ARM_TREATMENT}:
            raise ValueError(f"{where}: arms 必须同时给出 control 与 treatment")
        arms = {}
        for arm in (ARM_CONTROL, ARM_TREATMENT):
            raw = raw_arms[arm]
            source = raw.get("source")
            if source not in {REAL_MODEL, MOCK}:
                raise ValueError(f"{where}.{arm}: source 必须是 {REAL_MODEL} 或 {MOCK}")
            arms[arm] = ArmOutcome(
                arm=arm,
                source=source,
                run_id=str(raw.get("run_id") or ""),
                success=raw.get("success"),
                wall_seconds=float(raw.get("wall_seconds", 0.0)),
                total_tokens=int(raw.get("total_tokens", 0)),
                errors=tuple(raw.get("errors", ())),
                privilege_violations=int(raw.get("privilege_violations", 0)),
            )
        outcomes.append(
            PairOutcome(sample_id=str(item.get("sample_id")), order=tuple(order), arms=arms)
        )
    declared = set(spec["holdout_samples"])
    missing = declared - {outcome.sample_id for outcome in outcomes}
    if missing:
        raise ValueError(f"结果文件缺少预注册样本：{sorted(missing)}")
    return outcomes


def command_check(args: argparse.Namespace) -> int:
    spec = load_json(args.spec)
    if audit_ledger() != 0:
        print("[错误] M0b 台账检查未通过，先修台账再谈 M6 正式运行")
        return 1
    manifest = load_json(MANIFEST)
    checks = validate_freeze(spec, manifest=manifest, ledger_rows=ledger_rows())
    for item in checks:
        print(f"[{'通过' if item.passed else '未通过'}] {item.name}: {item.detail}")
    if freeze_passed(checks):
        print(f"冻结检查通过：可以按 {spec['experiment_id']} 启动正式运行")
        return 0
    failed = sum(1 for item in checks if not item.passed)
    print(f"冻结检查未通过：{failed} 项（不得启动 M6 正式运行）")
    return 1


def command_plan(args: argparse.Namespace) -> int:
    spec = load_json(args.spec)
    manifest = load_json(MANIFEST)
    rows = ledger_rows()
    checks = validate_freeze(spec, manifest=manifest, ledger_rows=rows)
    if not freeze_passed(checks):
        for item in checks:
            if not item.passed:
                print(f"[未通过] {item.name}: {item.detail}")
        print("冻结检查未通过：拒绝生成配对计划")
        return 1
    assignments = assert_frozen(spec, manifest=manifest, ledger_rows=rows)
    plan = {
        "schema_version": 1,
        "experiment_id": spec["experiment_id"],
        "candidate_id": spec["candidate_id"],
        "task_family": spec["task_family"],
        "seed": spec["seed"],
        "pairs": len(assignments),
        "assignments": assignments,
        "frozen_arms": {
            arm: {
                key: spec["arms"][arm][key]
                for key in ("model", "tools_revision", "budget_micros", "max_total_tokens")
            }
            for arm in (ARM_CONTROL, ARM_TREATMENT)
        },
        "note": "顺序由 (seed, sample_id) 决定，与结果无关；两臂配置必须保持一致",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"已写出配对计划 {args.output}（{len(assignments)} 对）")
    return 0


def command_analyze(args: argparse.Namespace) -> int:
    spec = load_json(args.spec)
    outcomes = load_outcomes(load_json(args.results), spec)
    analysis = analyze_pairs(spec, outcomes)
    markdown = render_analysis(analysis, spec)
    print(markdown)
    if args.markdown is not None:
        args.markdown.parent.mkdir(parents=True, exist_ok=True)
        args.markdown.write_text(markdown, encoding="utf-8")
        print(f"已写出报告片段 {args.markdown}")
    if args.output is not None:
        payload = {
            "schema_version": 1,
            "experiment_id": spec["experiment_id"],
            "verdict": analysis.verdict,
            "claim": analysis.claim,
            "reasons": list(analysis.reasons),
            "analysis": {
                key: value
                for key, value in asdict(analysis).items()
                if key not in {"reasons", "claim"}
            },
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8"
        )
        print(f"已写出机器可读判定 {args.output}")
    return 0


def command_record(args: argparse.Namespace) -> int:
    """把这次正式使用追加进台账；台账只追加，不做任何覆盖。"""

    if not args.confirm_formal_run:
        print("[错误] 登记正式使用会永久消耗 holdout，必须显式给出 --confirm-formal-run")
        return 1
    spec = load_json(args.spec)
    manifest = load_json(MANIFEST)
    if audit_ledger() != 0:
        print("[错误] M0b 台账检查未通过，拒绝登记")
        return 1
    assert_frozen(spec, manifest=manifest, ledger_rows=ledger_rows())
    outcomes = load_outcomes(load_json(args.results), spec)
    row = build_ledger_row(spec, outcomes)
    with RUN_LEDGER.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"已登记正式使用 {row['run_id']}（{len(outcomes)} 对）")
    if audit_ledger() != 0:
        print("[错误] 登记后台账检查未通过，请人工复核台账文件")
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--spec", type=Path, default=DEFAULT_SPEC)

    check = sub.add_parser("check", parents=[common], help="冻结检查")
    check.set_defaults(func=command_check)

    plan = sub.add_parser("plan", parents=[common], help="生成配对计划")
    plan.add_argument("--output", type=Path, default=ROOT / "output/m6-pair-plan.json")
    plan.set_defaults(func=command_plan)

    analyze = sub.add_parser("analyze", parents=[common], help="判定结果文件")
    analyze.add_argument("--results", type=Path, required=True)
    analyze.add_argument("--markdown", type=Path, default=None)
    analyze.add_argument("--output", type=Path, default=None)
    analyze.set_defaults(func=command_analyze)

    record = sub.add_parser("record", parents=[common], help="把正式使用追加进台账")
    record.add_argument("--results", type=Path, required=True)
    record.add_argument("--confirm-formal-run", action="store_true")
    record.set_defaults(func=command_record)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
