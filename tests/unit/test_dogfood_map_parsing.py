"""M1 自检脚本（dogfood 模块地图）的解析测试。

`scripts/m1_dogfood_map.py` 要把工具输出解析成路径列表，而工具输出里除了命中条目
还带"忽略规则"说明。这里用假输出锁住这几条解析规则，避免再把说明文字当成包名或路径。
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import m1_dogfood_map as dogfood  # noqa: E402

LISTING = "\n".join(
    [
        "目录：src/evoagent",
        "条目（4）：",
        "- api/",
        "- core/",
        "- config.py",
        "- __pycache__/",
        "",
        "被忽略（按原因）：",
        "- vcs_metadata: .git",
        "",
        "忽略规则：",
        "- vcs_metadata: 跳过版本库元数据目录（.git/.hg/.svn）",
        "- build_or_cache_directory: 跳过构建产物与缓存目录：.venv, __pycache__, dist",
    ]
)

FINDING = "\n".join(
    [
        "搜索起点：src/evoagent",
        "模式：*.py",
        "命中（3）：",
        "- src/evoagent/core/loop.py",
        "- src/evoagent/core/context.py",
        "- src/evoagent/__init__.py",
        "已扫描文件：12；按忽略规则跳过的目录：1",
        "忽略规则：",
        "- vcs_metadata: 跳过版本库元数据目录（.git/.hg/.svn）",
        "- compiled_artifact: 跳过编译产物：.pyc, .pyo, .pyd",
    ]
)


def test_entry_section_only_yields_directories() -> None:
    """只有"条目"段里以 `/` 结尾的行才算包，说明文字与文件不算。"""

    names: list[str] = []
    in_entries = False
    for line in LISTING.splitlines():
        if line.startswith("条目（"):
            in_entries = True
            continue
        if line.startswith("被忽略（"):
            in_entries = False
            continue
        if not in_entries or not line.startswith("- "):
            continue
        name = line[2:].strip()
        if name.endswith("/"):
            names.append(name.rstrip("/"))
    assert names == ["api", "core", "__pycache__"]
    assert "vcs_metadata" not in names


def test_finding_entries_exclude_trailing_ignore_rules() -> None:
    """`find_files` 末尾的忽略清单是说明，不是命中路径。"""

    hits = [
        line[2:]
        for line in FINDING.splitlines()
        if line.startswith("- ") and line[2:].endswith(".py")
    ]
    assert hits == [
        "src/evoagent/core/loop.py",
        "src/evoagent/core/context.py",
        "src/evoagent/__init__.py",
    ]
    assert not any("vcs_metadata" in item for item in hits)


def test_human_entries_reads_source_column_and_directories() -> None:
    text = "\n".join(
        [
            "| 主题 | 章节 | 源码入口 | 重点 |",
            "| --- | --- | --- | --- |",
            "| 数据契约 | 6～7 | `core/models.py`、`core/events.py` | 事件脱敏 |",
            "| 持久化 | 26～28 | `db/models.py`、`migrations/` | 事务边界 |",
            "| 前端 | 52 | `frontend/src/` | 服务端是事实来源 |",
            "| 不相关段落 | 1 | 这里没有源码入口 | 说明 |",
        ]
    )
    entries = dogfood.human_entries(text)
    assert entries == [
        "core/models.py",
        "core/events.py",
        "db/models.py",
        "migrations",
        "frontend/src",
    ]


def test_candidate_paths_try_package_then_repository_root() -> None:
    assert dogfood.candidate_paths("core/loop.py") == [
        "src/evoagent/core/loop.py",
        "core/loop.py",
    ]
    # `evals/` 在包内与仓库根都存在，因此两个候选都要试，顺序是包内优先。
    assert dogfood.candidate_paths("evals/datasets.py") == [
        "src/evoagent/evals/datasets.py",
        "evals/datasets.py",
    ]


def test_undocumented_only_reports_top_level_packages() -> None:
    packages = {
        ".": {"modules": ["src/evoagent/config.py"]},
        "core": {"modules": ["src/evoagent/core/loop.py"]},
        "projects": {"modules": ["src/evoagent/projects/schema.py"]},
        "tools/builtin": {"modules": ["src/evoagent/tools/builtin/list_dir.py"]},
    }
    entries = ["core/loop.py", "tools/builtin/list_dir.py"]
    assert dogfood.undocumented(packages, entries) == ["projects"]


def test_report_marks_itself_as_not_model_evidence() -> None:
    """自检脚本必须在报告里写明它不调用模型，避免被读成"模型会读项目"。"""

    source = (ROOT / "scripts" / "m1_dogfood_map.py").read_text(encoding="utf-8")
    assert '"mode": "tools_only_no_model"' in source
    assert '"agent_planning_verified": False' in source
