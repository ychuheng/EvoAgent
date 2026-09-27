"""M1 采集器"文件依据"解析规则的测试。

验收要求"必须列出至少三个可打开的文件依据；错误路径和编造组件记为失败"。
采集器据此从答复里挑出像路径的条目并逐条打开，规则必须既不过宽（把说明文字当路径）
也不过窄（漏掉真实路径）。
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import m1_project_read as collector  # noqa: E402


def test_extracts_paths_and_ignores_prose() -> None:
    answer = "\n".join(
        [
            "入口与模块如下：",
            "- src/fieldnotes/cli.py: `def main(` 是命令入口。",
            "- src/fieldnotes/store.py: 负责 JSON 存取。",
            "- 拒绝原因：路径超出已授权项目根。",
            "- 我可用的范围仅限已授权项目根内的文件。",
            "- README.md",
        ]
    )
    assert collector.evidence_candidates(answer) == [
        "README.md",
        "src/fieldnotes/cli.py",
        "src/fieldnotes/store.py",
    ]


def test_rejects_components_that_are_not_paths() -> None:
    """编造组件名（无后缀、无分隔符）不应被当成文件依据。"""

    answer = "\n".join(
        [
            "- PersistenceService: 我推测的组件",
            "- renderer: 另一个推测",
            "- src/real.py: 真实文件",
        ]
    )
    assert collector.evidence_candidates(answer) == ["src/real.py"]


def test_strips_trailing_punctuation_from_paths() -> None:
    answer = "- src/app.py，负责入口。\n- tests/test_app.py：测试"
    assert collector.evidence_candidates(answer) == ["src/app.py", "tests/test_app.py"]


def test_ignores_non_bullet_lines() -> None:
    answer = "src/loose.py 出现在正文里，但没有条目符号。\n- src/listed.py"
    assert collector.evidence_candidates(answer) == ["src/listed.py"]


def test_report_declares_it_is_not_model_evidence() -> None:
    source = (ROOT / "scripts" / "m1_project_read.py").read_text(encoding="utf-8")
    assert '"agent_project_understanding_verified": False' in source
