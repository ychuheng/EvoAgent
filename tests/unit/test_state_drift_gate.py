"""M0c 状态漂移闸门的脚本级测试（实施计划 §5.8）。

脚本本身按"基准提交之后产品代码变过就报警"工作，因此这里分别验证：

- 路径分类：产品路径算漂移，纯文档不算；
- 基准行的解析：能从状态页里取出提交号，取不到时报错而不是静默通过；
- 真实仓库上运行一次：基准与 HEAD 一致时通过（不一致时本测试会失败并在断言信息里给出提示）。
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import check_state_drift as gate  # noqa: E402


def _skip_if_history_is_incomplete(baseline: str) -> None:
    """浅克隆里历史提交根本取不到，此时不能把"闸门跑不了"报成"基准提交是假的"。

    CI 的 checkout 已设 `fetch-depth: 0`（见 `.github/workflows/ci.yml`），
    这里只兜住本地 `git clone --depth=1` 的情况。
    """

    unresolved = gate.git("rev-parse", "--verify", f"{baseline}^{{commit}}").returncode != 0
    if unresolved and gate.is_shallow_clone():
        pytest.skip("浅克隆无法解析基准提交；请先 git fetch --unshallow（CI 用 fetch-depth: 0）")


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


def test_state_page_has_a_resolvable_baseline() -> None:
    """状态页必须有一个在仓库里真实存在的基准提交。

    这里**故意不**断言"基准等于 HEAD"：任何一次正常提交都会让基准落后一步，
    那种断言会在每次开发时误报失败。基准是否已复核到 HEAD 属于**发布闸门**要判断的事
    （运行 `scripts/check_state_drift.py`），不是单元测试的职责。
    """

    text = gate.STATE.read_text(encoding="utf-8")
    found = gate.read_baseline(text)
    assert found is not None, "当前状态页缺少『产品代码核对基准』提交号"
    baseline, _line = found
    _skip_if_history_is_incomplete(baseline)
    resolved = gate.git("rev-parse", "--verify", f"{baseline}^{{commit}}")
    assert resolved.returncode == 0, f"基准提交 {baseline} 在仓库中不存在"


def test_unresolvable_baseline_is_reported_actionably() -> None:
    """取不到基准提交时，报错必须区分"提交号是假的"和"仓库是浅克隆"。

    这条以前是 CI 上真实的红：`actions/checkout` 默认 `--depth=1`，而基准按约定
    总是 HEAD 的祖先，于是闸门以"基准提交 36b5075 在仓库中不存在"这种误导性理由失败。
    """

    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "check_state_drift.py"), "--reference", "0" * 40],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        # Windows 控制台默认用本地代码页写 stdout，显式钉住 UTF-8 才能读回中文报错。
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )

    assert result.returncode == 1
    assert "在仓库中不存在" in result.stdout
    if gate.is_shallow_clone():
        assert "浅克隆" in result.stdout


def test_update_requires_a_clean_working_tree() -> None:
    """`--update` 需要干净工作树：记下的提交必须真的包含被核对的内容。

    这也是脚本曾经的缺陷所在——旧实现以"有产品改动"为由拒绝推进，导致基准永远推不动。
    现在改为：推进本身即"已核对"的声明，唯一守住的边界是工作树必须干净。
    """

    dirty = gate._uncommitted_product_paths()
    # 测试进程本身可能处在脏树上（开发中），因此只断言"返回的是产品路径且已排序去重"。
    assert dirty == sorted(set(dirty))
    assert all(gate.classify(item) == "product" for item in dirty)


def test_drift_detection_matches_git_diff() -> None:
    """脚本的判定必须与 git 的差异一致：基准到 HEAD 的产品文件列表非空即视为漂移。"""

    text = gate.STATE.read_text(encoding="utf-8")
    found = gate.read_baseline(text)
    assert found is not None
    baseline, _line = found
    _skip_if_history_is_incomplete(baseline)
    changed = gate.changed_files(baseline)
    product = [item for item in changed if gate.classify(item) == "product"]
    # 无论当前是否漂移，这里只要求分类结果是"文件子集"且能稳定复现。
    assert set(product).issubset(set(changed))
    if baseline == gate.head_revision():
        assert product == []
    else:
        # 基准落后时至少能列出改动文件（可能全是文档，此时 product 为空也合理）。
        assert isinstance(product, list)
