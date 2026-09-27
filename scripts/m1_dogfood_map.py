"""M1 收尾：在只读授权下用 EvoAgent 自身做一次"模块地图"核对（计划 §6 用户流程最后一句）。

做法：

1. 把仓库根登记为**只读**项目（`normalize_root` + `ProjectAuthorization.READ`）；
2. 只用 M1 的三条发现工具（`list_dir`/`find_files`/`search_text`）和 `file_read` 建立模块地图，
   不读任何仓库外的路径；
3. 抽取 `docs/源码地图.md` 第 2 节"源码入口"列里的人写路径（它是人工维护的对照物），
   用同一批工具逐条核对是否真实存在；
4. 反向检查：产品代码里存在、但源码地图未列出的新模块（例如本轮新增的 `projects/`、
   `runtime/budget.py`）都会被列出来，避免地图悄悄过期。

它**不**调用模型：这一步验证的是"工具能否在一个真实的中型仓库里按路径与内容定位文件、
并给出可回溯证据"，不是"模型会不会自己规划读取"。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from evoagent.projects.schema import ProjectAuthorization, normalize_root
from evoagent.projects.service import ActiveProject
from evoagent.tools.base import ToolExecutionError, ToolPermissionError
from evoagent.tools.builtin.find_files import FindFilesArguments, FindFilesTool
from evoagent.tools.builtin.list_dir import ListDirArguments, ListDirTool
from evoagent.tools.builtin.project_file_read import (
    ProjectFileReadArguments,
    ProjectFileReadTool,
)
from evoagent.tools.builtin.search_text import SearchTextArguments, SearchTextTool

ROOT = Path(__file__).resolve().parents[1]
SOURCE_MAP = ROOT / "docs" / "源码地图.md"
# 只匹配形如 `core/models.py` 的反引号片段，忽略 `src/evoagent/` 这类目录与 markdown 链接。
ENTRY_PATTERN = re.compile(r"`([A-Za-z0-9_./-]+\.(?:py|toml|yml|yaml|json|md))`")


def human_entries(text: str) -> list[str]:
    """从源码地图里抽出人写的源码入口（第 2 节表格）。

    只收反引号里出现**目录**（以 `/` 结尾）或**文件名**的片段；`src/evoagent/` 这类纯目录
    也算覆盖证据，因为它明确指向一个包。
    """

    entries: list[str] = []
    for line in text.splitlines():
        if not line.startswith("| ") or "`" not in line:
            continue
        cells = [item.strip() for item in line.strip().strip("|").split("|")]
        if len(cells) < 3:
            continue
        # 只取"源码入口"那一列，避免把章节索引里的其它反引号也算进来。
        for fragment in re.findall(r"`([^`]+)`", cells[2]):
            if fragment.endswith("/"):
                candidate = fragment.rstrip("/")
            elif ENTRY_PATTERN.fullmatch(f"`{fragment}`"):
                candidate = fragment
            else:
                continue
            if candidate not in entries:
                entries.append(candidate)
    return entries


def candidate_paths(entry: str) -> list[str]:
    """把地图里的写法映射成可能的仓库相对路径。

    地图里的入口多数写作 `core/models.py`（相对 `src/evoagent/`），少数写作完整仓库路径。
    注意 `evals/` 在两种层级都存在：包在 `src/evoagent/evals/`，数据集在仓库根的 `evals/`，
    因此对每个入口都按"包内优先、仓库根其次"的顺序尝试。
    """

    candidates = [f"src/evoagent/{entry}", entry]
    return candidates


async def build_toolkit(root: Path) -> dict:
    return {
        "list_dir": ListDirTool(root),
        "find_files": FindFilesTool(root),
        "search_text": SearchTextTool(root),
        "file_read": ProjectFileReadTool(root),
    }


async def package_map(tools: dict, root: Path) -> tuple[dict[str, dict], list[str]]:
    """用发现工具建立包级模块地图：包 -> 模块 -> 定义的关键符号。

    返回 (地图, `list_dir` 直接列出的包名)。两者来源不同（遍历 vs 列举），
    因此可以互相印证"发现"这一步不是靠猜。
    """

    listing = await tools["list_dir"].invoke(ListDirArguments(path="src/evoagent"))
    # `list_dir` 还会输出"条目（N）"、"被忽略（按原因）"与"忽略规则"等说明块，
    # 因此只在"条目"段内取以 `/` 结尾的行，避免把说明文字当成包名。
    listed_packages: list[str] = []
    in_entries = False
    for line in listing.splitlines():
        if line.startswith("条目（"):
            in_entries = True
            continue
        if line.startswith("被忽略（"):
            in_entries = False
            continue
        if not in_entries or not line.startswith("- "):
            continue
        name = line[2:].strip()
        # 地图的键是相对 `src/evoagent` 的包名，这里就把它归一成同一种写法。
        if name.endswith("/"):
            listed_packages.append(name.rstrip("/").removeprefix("src/evoagent/"))
    listed_packages = sorted(set(listed_packages))
    found = await tools["find_files"].invoke(
        # 用 path 限定范围 + 简单模式：`**/` 在本实现的匹配语义下不跨多级目录。
        FindFilesArguments(pattern="*.py", path="src/evoagent", max_results=500)
    )
    modules: dict[str, list[str]] = {}
    for line in found.splitlines():
        if not line.startswith("- "):
            continue
        path = line[2:]
        # 只收 .py 命中，避免把输出末尾的"忽略规则"清单当成路径。
        if not path.endswith(".py") or "__pycache__" in path:
            continue
        packages_of = path.removeprefix("src/evoagent/").rsplit("/", 1)
        package = packages_of[0] if len(packages_of) > 1 else "."
        modules.setdefault(package, []).append(path)

    symbols: dict[str, list[str]] = {}
    for package in modules:
        search_root = f"src/evoagent/{package}" if package != "." else "src/evoagent"
        try:
            result = await tools["search_text"].invoke(
                SearchTextArguments(query="def ", path=search_root, max_matches=400)
            )
        except (ToolExecutionError, ToolPermissionError):
            # 包目录可能只含子包（例如 api/ 下才是 routes/）；该层没有可直接搜的文件。
            symbols[package] = []
            continue
        names = sorted(
            {
                match.group(1)
                for match in re.finditer(r":\d+: (?:async )?def ([A-Za-z_][A-Za-z0-9_]*)", result)
            }
        )
        symbols[package] = names
    return {
        package: {"modules": sorted(paths), "symbols": symbols.get(package, [])}
        for package, paths in modules.items()
    }, listed_packages


async def check_entries(tools: dict, entries: list[str], root: Path) -> list[dict]:
    """逐条核对人写入口。

    文件用 `file_read` 打开（能读到第一行即算存在）；目录用 `list_dir` 列举
    （能列举即算存在）。两者都经同一套越界检查，因此这一步同时验证了工具在真实
    仓库上的可用性与边界。
    """

    checked: list[dict] = []
    for entry in entries:
        resolved: str | None = None
        is_directory = entry.endswith("/") or not Path(entry).suffix
        for candidate in candidate_paths(entry):
            try:
                if is_directory:
                    await tools["list_dir"].invoke(ListDirArguments(path=candidate))
                else:
                    await tools["file_read"].invoke(
                        ProjectFileReadArguments(path=candidate, max_lines=1)
                    )
            except (ToolExecutionError, ToolPermissionError):
                continue
            resolved = candidate
            break
        checked.append(
            {
                "entry": entry,
                "resolved": resolved,
                "kind": "directory" if is_directory else "file",
                "found": resolved is not None,
            }
        )
    return checked


def undocumented(packages: dict[str, dict], entries: list[str]) -> list[str]:
    """产品包目录里没有被源码地图任何入口覆盖到的**顶层**包。

    子包不单独判定：地图按主题列文件（例如 `tools/builtin/calculator.py`），
    因此 `tools/builtin` 这种子包是否"有覆盖"由它下面的文件决定，而不是由它自己出现的次数决定。
    """

    covered = {item.split("/")[0] for item in entries if "/" in item}
    return sorted(
        package
        for package, item in packages.items()
        if package != "."
        and "/" not in package
        and package not in covered
        and any(path.endswith(".py") for path in item["modules"])
    )


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--output", type=Path, default=ROOT / "output/m1-dogfood-map.json")
    args = parser.parse_args()

    normalized = normalize_root(str(args.root))
    # 只读授权：这一步不需要、也不授予写权限。
    project = ActiveProject(
        id=uuid4(),
        name=normalized.path.name,
        root=normalized.path,
        authorization=ProjectAuthorization.READ,
        authorization_version=1,
    )
    tools = await build_toolkit(project.root)

    started = time.perf_counter()
    packages, listed = await package_map(tools, project.root)
    map_seconds = time.perf_counter() - started

    entries = human_entries(SOURCE_MAP.read_text(encoding="utf-8"))
    started = time.perf_counter()
    checked = await check_entries(tools, entries, project.root)
    verify_seconds = time.perf_counter() - started

    missing = [item["entry"] for item in checked if not item["found"]]
    gaps = undocumented(packages, entries)
    report = {
        "schema_version": 1,
        "created_at": datetime.now(UTC).isoformat(),
        "mode": "tools_only_no_model",
        "scope": (
            "M1 dogfood: build and verify a module map of EvoAgent itself with the read-only "
            "project tools; not a check of model planning"
        ),
        "agent_planning_verified": False,
        "authorization": project.authorization.value,
        "root": project.root.as_posix(),
        "package_count": len(packages),
        "module_count": sum(len(item["modules"]) for item in packages.values()),
        "listed_packages": listed,
        "listed_not_in_map": [item for item in listed if item not in packages],
        "map_seconds": round(map_seconds, 3),
        "verify_seconds": round(verify_seconds, 3),
        "packages": packages,
        "human_entries_total": len(entries),
        "human_entries_found": len(checked) - len(missing),
        "human_entries_missing": missing,
        "undocumented_packages": gaps,
        "passed": not missing,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        f"包 {report['package_count']} 个、模块 {report['module_count']} 个；"
        f"源码地图入口 {report['human_entries_found']}/{report['human_entries_total']} 命中；"
        f"地图耗时 {report['map_seconds']}s、核对耗时 {report['verify_seconds']}s"
    )
    if missing:
        print("未命中的入口：" + "、".join(missing))
    if gaps:
        print("源码地图未覆盖的产品包：" + "、".join(gaps))
    print(f"输出 {args.output}")
    return 0 if not missing else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
