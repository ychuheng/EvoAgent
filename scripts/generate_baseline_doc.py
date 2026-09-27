"""把 `output/m0-baseline.json` 渲染成可提交的基线文档（计划 §5.6）。

文档由数据生成，而不是手抄：避免"文档里的耗时与工具清单和实测脱节"。
用法：
    python scripts/m0_baseline.py            # 先实测并写出 output/m0-baseline.json
    python scripts/generate_baseline_doc.py  # 再由 JSON 生成文档
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = ROOT / "output/m0-baseline.json"
DEFAULT_TARGET = ROOT / "docs/evaluations/基线记录-2026-09-27.md"


def _yes_no(value: bool) -> str:
    return "是" if value else "否"


def _configured(value: bool) -> str:
    return "已配置" if value else "未配置"


def render(report: dict) -> str:
    env = report["environment"]
    provider = report["provider"]
    search = report["search"]
    tools = report["tools"]
    budget = report["budget"]
    context = report["context"]
    frontend = report["frontend"]
    dist_hash = (frontend["dist_sha256"] or "")[:16]

    sections: list[str] = [
        "# 基线记录（计划 §5.6，2026-09-27）",
        "",
        "> 本文件由 `scripts/generate_baseline_doc.py` 从实测的 `output/m0-baseline.json` 生成，",
        "> 不要手工编辑：`python scripts/m0_baseline.py` 会重跑全部采集器并刷新数据。",
        ">",
        "> 计划要求记录当前 Mock/真实模型、搜索、工具清单、前端交互和耗时作为基线，并把",
        "> 「代码存在」「受控测试通过」「真实用户任务通过」分开。**下表「真实任务」一栏全部是",
        "> `not_verified`**：本仓库至今没有真实模型端到端证据。",
        "",
        "## 1. 环境",
        "",
        "| 项 | 值 |",
        "| --- | --- |",
        f"| 提交 | `{env['commit']}`（分支 `{env['branch']}`） |",
        f"| 工作树 | {'有未提交改动' if env['dirty'] else '干净'} |",
        f"| Python | {env['python']} |",
        f"| 平台 | {env['platform']} |",
        f"| 迁移 head | `{report['migrations']['head']}` |",
        f"| 前端构建产物 | {'存在' if frontend['dist_present'] else '缺失'} |",
        f"| 构建产物 SHA-256 | `{dist_hash}`… |",
        "",
        "## 2. Provider、搜索与预算",
        "",
        "| 项 | 值 |",
        "| --- | --- |",
        f"| Provider 模式 | {provider['mode']}（`{provider['provider']}`） |",
        f"| 模型 | `{provider['model']}` |",
        f"| 模型凭据 | {_configured(provider['api_key_configured'])}；"
        f"Base URL {_configured(provider['base_url_configured'])} |",
        f"| 搜索提供方 | `{search['mode']}`（密钥{_configured(search['api_key_configured'])}） |",
        f"| 记忆召回 | {'开启' if report['memory']['retrieval_enabled'] else '关闭'} |",
        f"| 上下文策略 | `{context['policy']}`，窗口 {context['window_tokens']} token，"
        f"总预算 {context['max_total_tokens']} |",
        f"| 预算闸门 scope | `{budget['scope']}` |",
        f"| 试跑额度 | {'已填' if budget['trial_limit_configured'] else '**未填**'} |",
        f"| 正式额度 | {'已填' if budget['total_limit_configured'] else '**未填**'} |",
        f"| 价格假设 | {'已填' if budget['price_configured'] else '**未填**'} |",
        "",
        f"> {budget['note']}。",
        "",
        "## 3. 工具清单",
        "",
        f"共 **{tools['count']}** 个；清单哈希 `{tools['manifest_hash'][:24]}…`"
        "（工具契约变化会让它变化）。",
        "",
        "| 工具 | 风险 | 副作用 | 实现版本 |",
        "| --- | --- | --- | --- |",
    ]
    for item in tools["catalog"]:
        sections.append(
            f"| `{item['name']}` | {item['risk']} | {_yes_no(item['side_effects'])} | "
            f"`{item['implementation_version']}` |"
        )
    allowlist = tools["project_command_allowlist"]
    allowlist_text = (
        "、".join(f"`{item}`" for item in allowlist)
        if allowlist
        else "**空**（默认不能运行任何命令）"
    )
    sections += [
        "",
        f"项目命令白名单：{allowlist_text}。",
        "",
        "## 4. 采集器实测耗时",
        "",
        "| 采集器 | 退出码 | 耗时（秒） | 末行输出 |",
        "| --- | --- | --- | --- |",
    ]
    for item in report["collectors"]:
        tail = item["stdout_tail"][0] if item["stdout_tail"] else ""
        sections.append(
            f"| `{item['script']}` | {item['returncode']} | {item['seconds']} | {tail} |"
        )
    if not report["collectors"]:
        sections.append("| （本次未重跑采集器） | — | — | — |")

    sections += [
        "",
        "## 5. 三级判定",
        "",
        "| 能力 | 当前级别 | 证据 | 真实任务 |",
        "| --- | --- | --- | --- |",
    ]
    for item in report["capability_levels"]:
        sections.append(
            f"| {item['capability']} | {item['level']} | {item['evidence']} | "
            f"`{item['real_task']}` |"
        )
    sections += [
        "",
        "## 6. 这份基线不能推断什么",
        "",
        f"> {report['note']}",
        "",
        "具体地说：",
        "",
        "- 采集器全部通过只说明**工具契约与边界**在受控序列下成立；它们的 `steps` 是脚本预设的，",
        "  不能读成「模型会自己规划读取/修改/诊断」。",
        "- 前端 22 项测试与构建通过说明**页面机制**可用，不含真实用户操作。",
        "- 工具清单哈希是**契约指纹**，用于发现工具面变化，不表示这些工具已被真实任务使用过。",
        "",
    ]
    return "\n".join(sections)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--target", type=Path, default=DEFAULT_TARGET)
    args = parser.parse_args()

    if not args.source.is_file():
        print(f"缺少 {args.source}；请先运行 python scripts/m0_baseline.py")
        return 1
    report = json.loads(args.source.read_text(encoding="utf-8"))
    args.target.parent.mkdir(parents=True, exist_ok=True)
    args.target.write_text(render(report) + "\n", encoding="utf-8")
    print(f"已写出 {args.target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
