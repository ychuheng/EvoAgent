"""计划 §5.6：记录当前基线——Mock/真实模型、搜索、工具清单、前端交互与耗时。

计划要求"记录当前 Mock/真实模型、搜索、工具清单、前端交互和耗时作为基线；
'代码存在''受控测试通过''真实用户任务通过'分开"。

因此这里的输出刻意分成两层：

- **machine**（`output/m0-baseline.json`）：环境、Provider、搜索、工具清单与哈希、
  迁移 head、前端构建产物哈希、各采集器的实测耗时；
- **three levels**：每条能力标注它当前处在哪一级（代码存在 / 受控测试通过 / 真实任务通过），
  真实任务一律为 `not_verified`，除非报告目录里存在对应的真实运行证据。

不采集任何密钥值：只记录 Provider 名称、模型名与是否配置。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from evoagent.config import ProviderName, Settings
from evoagent.tools.catalog import default_skill_tool_catalog

ROOT = Path(__file__).resolve().parents[1]
COLLECTORS = (
    ("m0a", "scripts/m0a_project_dev.py"),
    ("m0-interaction-skill", "scripts/m0_interaction_skill.py"),
    ("m1-project-read", "scripts/m1_project_read.py"),
    ("m1-dogfood-map", "scripts/m1_dogfood_map.py"),
    ("m2-project-edit", "scripts/m2_project_edit.py"),
    ("m3-failure-fix", "scripts/m3_failure_fix.py"),
    ("m5-acceptance", "scripts/m5_acceptance.py"),
)
# 每一项能力当前处在哪一级；`real` 一栏是本脚本**不**会自己填成通过的部分。
CAPABILITIES: tuple[tuple[str, str, str], ...] = (
    ("Agent 内核与持久化执行", "受控测试通过", "脚本化 Mock 与集成测试"),
    ("项目只读（M1）", "受控测试通过", "自检脚本 + Mock 采集器 5/5"),
    ("精确编辑（M2）", "受控测试通过", "Mock 采集器 4/4"),
    ("命令执行与失败修正（M3）", "受控测试通过", "Mock 采集器 2/2"),
    ("交互与时间线（M4）", "受控测试通过", "前端 22 项 + 后端集成测试"),
    ("产物出口与目录整理（M5）", "受控测试通过", "7/7（含失败样本）"),
    ("格式处理（M5 F-03）", "受控测试通过", "22 项单元测试"),
    ("Skill 收益（M6）", "代码存在", "机制完整，收益未证实"),
    ("发布级回归（M7）", "代码存在", "未开始"),
)


def git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", check=False
    )
    return result.stdout.strip()


def digest_tree(root: Path) -> str | None:
    if not root.is_dir():
        return None
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def migration_head() -> str | None:
    versions = sorted((ROOT / "migrations" / "versions").glob("*.py"))
    heads = [item.stem for item in versions]
    return heads[-1].split("_")[0] + "_" + heads[-1].split("_")[1] if heads else None


def run_collector(script: str) -> dict:
    """真实跑一遍采集器并记录耗时：基线里的耗时不靠估计。"""

    started = time.perf_counter()
    result = subprocess.run(
        [sys.executable, "-X", "utf8", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    elapsed = time.perf_counter() - started
    return {
        "script": script,
        "returncode": result.returncode,
        "seconds": round(elapsed, 3),
        "stdout_tail": (result.stdout or "").strip().splitlines()[-1:] or [],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "output/m0-baseline.json")
    parser.add_argument("--skip-collectors", action="store_true", help="不重跑采集器（只记录环境）")
    args = parser.parse_args()

    settings = Settings()
    registry = default_skill_tool_catalog()
    tools = []
    for name in sorted(registry.names):
        definition = registry.get(name)
        tools.append(
            {
                "name": name,
                "risk": definition.risk.value,
                "side_effects": definition.has_side_effects,
                "implementation_version": definition.implementation_version,
            }
        )
    tool_manifest_hash = registry.manifest_hash()

    collectors = [] if args.skip_collectors else [run_collector(script) for _, script in COLLECTORS]

    report = {
        "schema_version": 1,
        "created_at": datetime.now(UTC).isoformat(),
        "levels": {
            "code_exists": "代码存在",
            "controlled_tests": "受控测试通过",
            "real_task": "真实用户任务通过",
        },
        "environment": {
            "commit": git("rev-parse", "--short", "HEAD"),
            "branch": git("rev-parse", "--abbrev-ref", "HEAD"),
            "dirty": bool(git("status", "--porcelain")),
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "os_name": os.name,
        },
        "provider": {
            "mode": "mock" if settings.provider is ProviderName.MOCK else "real",
            "provider": settings.provider.value,
            "model": settings.model or "mock-model",
            "base_url_configured": settings.base_url is not None,
            "api_key_configured": settings.api_key is not None,
        },
        "search": {
            "mode": settings.search_provider,
            "api_key_configured": settings.search_api_key is not None,
        },
        "memory": {"retrieval_enabled": settings.memory_retrieval_enabled},
        "context": {
            "policy": settings.context_policy,
            "window_tokens": settings.context_window_tokens,
            "max_total_tokens": settings.max_total_tokens,
        },
        "budget": {
            "scope": settings.budget_scope,
            "trial_limit_configured": settings.budget_trial_limit_micros is not None,
            "total_limit_configured": settings.budget_total_limit_micros is not None,
            "price_configured": settings.budget_input_price_micros_per_million is not None,
            "note": "未填数值时预算闸门会拒绝付费模型调用",
        },
        "tools": {
            "catalog": tools,
            "count": len(tools),
            "manifest_hash": tool_manifest_hash,
            "project_command_allowlist": list(settings.project_command_allowlist),
        },
        "migrations": {"head": migration_head()},
        "frontend": {
            "dist_present": (ROOT / "frontend" / "dist").is_dir(),
            "dist_sha256": digest_tree(ROOT / "frontend" / "dist"),
        },
        "collectors": collectors,
        "capability_levels": [
            {"capability": name, "level": level, "evidence": evidence, "real_task": "not_verified"}
            for name, level, evidence in CAPABILITIES
        ],
        "note": (
            "真实任务一栏一律为 not_verified：本仓库至今没有真实模型端到端证据。"
            "任何把它读成'已通过'的解读都超出了这份基线能支持的范围。"
        ),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        f"基线已写出：{args.output}（工具 {len(tools)} 个、采集器 {len(collectors)} 个、"
        f"commit {report['environment']['commit']}）"
    )
    failed = [item for item in collectors if item["returncode"] != 0]
    if failed:
        print("以下采集器未通过：")
        for item in failed:
            print(f"  - {item['script']} (rc={item['returncode']})")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
