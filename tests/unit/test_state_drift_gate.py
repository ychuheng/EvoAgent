"""M0c 状态漂移闸门的脚本级测试（实施计划 §5.8）。

脚本本身按"基准提交之后产品代码变过就报警"工作，因此这里分别验证：

- 路径分类：产品路径算漂移，纯文档不算；
- 基准行的解析：能从状态页里取出提交号，取不到时报错而不是静默通过；
- 真实仓库上运行一次：基准与 HEAD 一致时通过（不一致时本测试会失败并在断言信息里给出提示）。
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import check_state_drift as gate  # noqa: E402


@pytest.mark.parametrize(
    "path",
    [
        "src/evoagent/api/app.py",
        "tests/unit/test_x.py",
        "scripts/m0a_project_dev.py",
        "migrations/versions/20260927_0019_spend_ledger.py",
        "frontend/src/pages/ChatPage.tsx",
        "deploy/personal/compose.yml",
        "evals/datasets/manifest.json",
        "pyproject.toml",
        "docker-compose.yml",
        "alembic.ini",
        ".github/workflows/ci.yml",
    ],
)
def test_product_paths_are_drift_relevant(path: str) -> None:
    assert gate.classify(path) == "product"


@pytest.mark.parametrize(
    "path",
    ["docs/当前状态.md", "docs/evaluations/人评表模板.md"],
)
def test_doc_paths_are_not_drift_relevant(path: str) -> None:
    # 文档更新不改变产品行为，因此不应阻断里程碑关闭。
    assert gate.classify(path) == "doc"


@pytest.mark.parametrize("path", ["README.md", "个人简历-杨博尧.pdf"])
def test_root_prose_is_never_product(path: str) -> None:
    # 根级说明文件不改变产品行为：分类可以是 other，但绝不能算产品漂移。
    assert gate.classify(path) != "product"


def test_read_baseline_extracts_commit_and_line() -> None:
    text = "\n".join(
        [
            "# 状态",
            "> - 产品代码核对基准：`c2` 分支 `62de451`（2026-09-27 复核后推进）",
            "> - 其他说明",
        ]
    )
    found = gate.read_baseline(text)
    assert found is not None
    commit, line = found
    assert commit == "62de451"
    assert gate.BASELINE_LABEL in line


def test_read_baseline_returns_none_without_commit() -> None:
    """状态页没有基准行时必须报错，不能静默放行。"""

    assert gate.read_baseline("> - 产品代码核对基准：尚未记录\n") is None
    assert gate.read_baseline("# 只有别的行\n") is None


def test_repository_baseline_matches_head() -> None:
    """真实仓库上跑一次：基准必须已复核到 HEAD（否则闸门会阻断里程碑关闭）。"""

    text = gate.STATE.read_text(encoding="utf-8")
    found = gate.read_baseline(text)
    assert found is not None, "当前状态页缺少『产品代码核对基准』提交号"
    baseline, _line = found
    head = gate.head_revision()
    changed = gate.changed_files(baseline)
    product = [item for item in changed if gate.classify(item) == "product"]
    assert not product, (
        f"基准 {baseline} 之后产品代码已改动（{len(product)} 个文件），"
        "里程碑不得关闭；请重新核对后运行 scripts/check_state_drift.py --update"
    )
    assert baseline == head or not changed
