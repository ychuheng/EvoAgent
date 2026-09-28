"""M0c 状态漂移闸门：核对基准提交与 HEAD 的产品代码是否已经漂移。

实施计划 §5.8 要求状态漂移检查脚本在正式验收前入库。做法是：

1. 从 `docs/当前状态.md` 读出"产品代码核对基准"里的提交号；
2. 比较该提交到 HEAD 之间**产品代码、配置、迁移与测试**的改动；
3. 有改动即判定漂移并返回非零，阻断里程碑关闭。

只改 `docs/`、根级说明文档等非产品路径**不算**漂移——文档更新不改变产品行为。

用法：
    python scripts/check_state_drift.py            # 漂移即报错
    python scripts/check_state_drift.py --update   # 核对完成后把基准提交推进到 HEAD
"""

from __future__ import annotations

import argparse
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "docs" / "当前状态.md"

BASELINE_LABEL = "产品代码核对基准"

# 产品交付面：这些路径下的改动意味着"产品代码已经前进"，必须重新核对。
PRODUCT_ROOTS = frozenset(
    {
        "src",
        "tests",
        "scripts",
        "migrations",
        "frontend",
        "deploy",
        "evals",
        ".github",
    }
)
PRODUCT_FILES = frozenset(
    {
        "pyproject.toml",
        "alembic.ini",
        "docker-compose.yml",
        "Dockerfile",
        ".pre-commit-config.yaml",
        ".dockerignore",
        ".env.example",
        ".env.personal.example",
    }
)
# 明确不算产品行为的路径；其余未列出的根级文件按"其他"处理，同样不判漂移。
DOC_ROOTS = frozenset({"docs"})

_COMMIT = re.compile(r"`([0-9a-f]{7,40})`")


def git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def head_revision() -> str:
    result = git("rev-parse", "--short", "HEAD")
    if result.returncode != 0:
        raise SystemExit("无法读取 HEAD：" + result.stderr.strip())
    return result.stdout.strip()


def current_branch() -> str:
    result = git("rev-parse", "--abbrev-ref", "HEAD")
    return result.stdout.strip() if result.returncode == 0 else "?"


def is_shallow_clone() -> bool:
    """仓库是否为浅克隆（`--depth`）。浅克隆里历史提交取不到，闸门无法工作。"""

    result = git("rev-parse", "--is-shallow-repository")
    return result.returncode == 0 and result.stdout.strip() == "true"


def _unresolved_baseline_reason(baseline: str) -> str:
    reason = f"基准提交 {baseline} 在仓库中不存在"
    if is_shallow_clone():
        reason += (
            "（当前是浅克隆：历史提交没有被取下来，闸门无法比较；"
            "请先 `git fetch --unshallow`，CI 上给 actions/checkout 设 `fetch-depth: 0`）"
        )
    return reason


def classify(path: str) -> str:
    parts = Path(path).parts
    if not parts:
        return "other"
    head = parts[0]
    if head in DOC_ROOTS:
        return "doc"
    if head in PRODUCT_ROOTS:
        return "product"
    if len(parts) == 1 and head in PRODUCT_FILES:
        return "product"
    return "other"


def read_baseline(text: str) -> tuple[str, str] | None:
    """返回 (提交号, 该行原文)。"""

    for line in text.splitlines():
        if BASELINE_LABEL in line:
            match = _COMMIT.search(line)
            if match:
                return match.group(1), line
    return None


def changed_files(baseline: str) -> list[str]:
    result = git("diff", "--name-only", f"{baseline}..HEAD")
    if result.returncode != 0:
        raise SystemExit(
            f"无法比较 {baseline}..HEAD：{result.stderr.strip()}"
            + ("（当前是浅克隆：请先 `git fetch --unshallow`）" if is_shallow_clone() else "")
        )
    return [item for item in result.stdout.splitlines() if item.strip()]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--update", action="store_true", help="把基准提交推进到 HEAD")
    parser.add_argument("--reference", help="覆盖当前状态里的基准提交（用于核对历史提交）")
    args = parser.parse_args()

    if not STATE.is_file():
        print(f"缺少 {STATE.relative_to(ROOT).as_posix()}")
        return 1
    text = STATE.read_text(encoding="utf-8")
    found = read_baseline(text)
    if found is None:
        print(f"{STATE.name} 里找不到含提交号的「{BASELINE_LABEL}」行")
        return 1
    baseline, original_line = found
    if args.reference:
        baseline = args.reference

    head = head_revision()
    branch = current_branch()

    resolved = git("rev-parse", "--verify", f"{baseline}^{{commit}}")
    if resolved.returncode != 0:
        print(_unresolved_baseline_reason(baseline))
        return 1
    ancestor = git("merge-base", "--is-ancestor", baseline, "HEAD")
    if ancestor.returncode != 0:
        print(
            f"基准提交 {baseline} 不是 HEAD 的祖先，无法按线性历史比较；"
            "请人工确认两段历史的关系后更新基准"
        )
        return 1

    files = changed_files(baseline)
    product = [item for item in files if classify(item) == "product"]
    doc = [item for item in files if classify(item) == "doc"]
    other = [item for item in files if classify(item) == "other"]

    print(f"基准提交：{baseline}（分支标注见 {STATE.name}）")
    print(f"当前 HEAD：{head}（{branch}）")
    print(
        f"{baseline}..HEAD 共 {len(files)} 个文件改动："
        f"产品 {len(product)}、文档 {len(doc)}、其他 {len(other)}"
    )

    if args.update:
        # `--update` 本身就是"我已核对过这些改动"的声明，因此**不能**再以"有改动"为由拒绝，
        # 否则基准永远无法推进。真正需要守住的边界是：被记录的提交必须已经包含被核对的内容，
        # 所以工作树必须干净——否则记的是一个不含那些改动的提交。
        if baseline == head:
            print("基准提交已经是 HEAD，无需更新")
            return 0
        dirty = _uncommitted_product_paths()
        if dirty:
            print("工作树有未提交改动，拒绝推进基准：记下的提交不会包含这些改动。")
            for item in dirty:
                print(f"  - {item}")
            print("请先提交，再运行：python scripts/check_state_drift.py --update")
            return 1
        new_line = original_line.replace(f"`{baseline}`", f"`{head}`", 1)
        STATE.write_text(text.replace(original_line, new_line, 1), encoding="utf-8")
        print(f"已把基准提交更新为 {head}（复核记录：{baseline}..{head}）")
        print(
            f"本次推进涉及产品 {len(product)} 个文件；"
            "未验证项必须已登记在状态页第 3 节与各验收报告的『仍未验证』小节。"
        )
        return 0

    if product:
        print("状态漂移：基准提交之后产品代码已改动，里程碑不得关闭。改动文件：")
        for item in product:
            print(f"  - {item}")
        print("重新核对后运行：python scripts/check_state_drift.py --update")
        return 1

    print("无产品代码漂移；基准提交可以推进")
    return 0


def _uncommitted_product_paths() -> list[str]:
    """工作树里未提交的产品路径；未跟踪的本地目录（output/ 等）不算。"""

    result = git("status", "--porcelain")
    if result.returncode != 0:
        return []
    paths: list[str] = []
    for line in result.stdout.splitlines():
        if len(line) < 4:
            continue
        path = line[3:].strip().strip('"')
        if " -> " in path:  # 重命名：取新路径
            path = path.split(" -> ", 1)[1]
        if classify(path) == "product":
            paths.append(path)
    return sorted(set(paths))


if __name__ == "__main__":
    raise SystemExit(main())
