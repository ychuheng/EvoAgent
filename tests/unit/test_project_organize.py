"""M5 F-05 目录整理的测试：计划、冲突清单、dry-run、执行与回滚、删除单独审批。

计划要求"移动/重命名先生成计划及冲突清单，支持 dry-run 和已执行清单；
对样例目录分类后可核对每个文件去向，失败可恢复"，以及"删除单独审批"。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from evoagent.projects.organize import MoveRule, apply_plan, plan_moves
from evoagent.projects.schema import ProjectAuthorization
from evoagent.tools.base import ToolPermissionError
from evoagent.tools.builtin.project_organize import (
    OrganizeFilesArguments,
    OrganizeFilesTool,
    OrganizeItem,
    project_organize_tools,
)


def make_messy_dir(root: Path) -> None:
    (root / "inbox").mkdir(parents=True)
    (root / "docs").mkdir()
    (root / "reports").mkdir()
    (root / "inbox" / "note-a.md").write_text("a", encoding="utf-8")
    (root / "inbox" / "note-b.md").write_text("b", encoding="utf-8")
    (root / "inbox" / "summary.pdf").write_bytes(b"%PDF-1.4\n")
    (root / "docs" / "taken.md").write_text("already here", encoding="utf-8")


def test_plan_separates_ready_conflicts_and_skipped(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    make_messy_dir(root)

    plan = plan_moves(
        root,
        [
            MoveRule(source="inbox/note-a.md", destination="docs/note-a.md"),
            # 目标已存在 → 冲突，不覆盖。
            MoveRule(source="inbox/note-b.md", destination="docs/taken.md"),
            # 源不存在 → 跳过。
            MoveRule(source="inbox/missing.md", destination="docs/missing.md"),
            # 目标目录不存在 → 跳过。
            MoveRule(source="inbox/summary.pdf", destination="archive/summary.pdf"),
            # 源与目标相同 → 跳过。
            MoveRule(source="docs/taken.md", destination="docs/taken.md"),
        ],
    )

    assert [item.source for item in plan.ready] == ["inbox/note-a.md"]
    assert [item.source for item in plan.conflicts] == ["inbox/note-b.md"]
    assert {item.source for item in plan.skipped} == {
        "inbox/missing.md",
        "inbox/summary.pdf",
        "docs/taken.md",
    }
    assert "不会覆盖" in plan.conflicts[0].reason
    # 计划阶段不得改动磁盘。
    assert (root / "inbox" / "note-a.md").exists()
    assert (root / "docs" / "note-a.md").exists() is False


def test_plan_detects_duplicate_targets_and_sources(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    make_messy_dir(root)

    same_target = plan_moves(
        root,
        [
            MoveRule(source="inbox/note-a.md", destination="docs/x.md"),
            MoveRule(source="inbox/note-b.md", destination="docs/x.md"),
        ],
    )
    assert len(same_target.conflicts) == 1
    assert "同一目标" in same_target.conflicts[0].reason

    same_source = plan_moves(
        root,
        [
            MoveRule(source="inbox/note-a.md", destination="docs/a.md"),
            MoveRule(source="inbox/note-a.md", destination="docs/b.md"),
        ],
    )
    assert len(same_source.conflicts) == 1
    assert "同一个源" in same_source.conflicts[0].reason


def test_plan_rejects_out_of_root_paths(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    make_messy_dir(root)
    outside = tmp_path / "outside"
    outside.mkdir()

    plan = plan_moves(root, [MoveRule(source="inbox/note-a.md", destination="../outside/x.md")])

    assert plan.ready == []
    assert len(plan.skipped) == 1
    assert "项目根" in plan.skipped[0].reason


def test_apply_moves_every_ready_item(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    make_messy_dir(root)
    plan = plan_moves(
        root,
        [
            MoveRule(source="inbox/note-a.md", destination="docs/note-a.md"),
            MoveRule(source="inbox/note-b.md", destination="reports/note-b.md"),
        ],
    )

    result = apply_plan(root, plan)

    assert result["failed"] is None
    assert result["rolled_back"] == []
    assert sorted(item["destination"] for item in result["executed"]) == [
        "docs/note-a.md",
        "reports/note-b.md",
    ]
    # 每个文件的去向可核对。
    assert (root / "docs" / "note-a.md").read_text(encoding="utf-8") == "a"
    assert (root / "reports" / "note-b.md").read_text(encoding="utf-8") == "b"
    assert not (root / "inbox" / "note-a.md").exists()


def test_apply_rolls_back_when_a_later_move_fails(tmp_path: Path) -> None:
    """执行中途失败必须回滚已完成的部分，并在结果里说明。"""

    root = tmp_path / "repo"
    make_messy_dir(root)
    plan = plan_moves(
        root,
        [
            MoveRule(source="inbox/note-a.md", destination="docs/note-a.md"),
            MoveRule(source="inbox/note-b.md", destination="reports/note-b.md"),
        ],
    )
    # 第二个目标的父目录在计划之后消失，模拟"计划与执行之间环境变了"。
    (root / "reports").rmdir()

    result = apply_plan(root, plan)

    assert result["failed"] is not None
    assert result["rolled_back"] == ["inbox/note-a.md"]
    assert (root / "inbox" / "note-a.md").exists()
    assert not (root / "docs" / "note-a.md").exists()


@pytest.mark.asyncio
async def test_tool_dry_run_only_reports(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    make_messy_dir(root)
    tool = OrganizeFilesTool(root, authorization=ProjectAuthorization.READ_WRITE)

    payload = json.loads(
        await tool.invoke(
            OrganizeFilesArguments(
                rules=[OrganizeItem(source="inbox/note-a.md", destination="docs/note-a.md")],
                dry_run=True,
            )
        )
    )

    assert payload["dry_run"] is True
    assert payload["plan"]["ready"] == 1
    assert "未修改任何文件" in payload["next_step"]
    assert (root / "inbox" / "note-a.md").exists()


@pytest.mark.asyncio
async def test_tool_applies_and_lists_every_destination(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    make_messy_dir(root)
    tool = OrganizeFilesTool(root, authorization=ProjectAuthorization.READ_WRITE)

    payload = json.loads(
        await tool.invoke(
            OrganizeFilesArguments(
                rules=[
                    OrganizeItem(source="inbox/note-a.md", destination="docs/note-a.md"),
                    OrganizeItem(source="inbox/note-b.md", destination="docs/taken.md"),
                ],
                dry_run=False,
            )
        )
    )

    assert payload["plan"]["conflicts"] == 1
    assert payload["result"]["executed"] == [
        {"source": "inbox/note-a.md", "destination": "docs/note-a.md"}
    ]
    # 冲突的目标必须原样保留，没有被覆盖。
    assert (root / "docs" / "taken.md").read_text(encoding="utf-8") == "already here"


@pytest.mark.asyncio
async def test_organize_requires_write_authorization(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    make_messy_dir(root)
    read_only = OrganizeFilesTool(root, authorization=ProjectAuthorization.READ)

    with pytest.raises(ToolPermissionError, match="只读授权"):
        await read_only.invoke(
            OrganizeFilesArguments(
                rules=[OrganizeItem(source="inbox/note-a.md", destination="docs/note-a.md")]
            )
        )
    assert project_organize_tools(root, authorization=ProjectAuthorization.READ) == []


def test_organize_tools_never_include_deletion(tmp_path: Path) -> None:
    """删除必须单独审批：整理工具集里不应出现任何删除能力。"""

    names = [
        tool.name
        for tool in project_organize_tools(tmp_path, authorization=ProjectAuthorization.READ_WRITE)
    ]
    assert names == ["organize_files"]
    assert not any("delete" in name for name in names)
