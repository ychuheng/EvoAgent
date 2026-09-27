"""M5 F-05：目录整理——先生成计划与冲突清单，dry-run 后再执行。

计划要求：

- 「移动/重命名**先生成计划及冲突清单**，支持 dry-run 和已执行清单」；
- 「删除**单独审批**」——因此这里只做移动/重命名，删除仍走 `delete_file` 那条独立路径；
- 「对样例目录分类后可核对每个文件去向，失败可恢复」——每个文件一条结果，
  执行中途失败按已移动的文件**逆序回滚**，并在结果里如实标出回滚情况。

冲突处理的核心约定：**目标已存在时不覆盖**。计划里把它列为冲突而不是"自动改名"，
因为自动改名会让"每个文件的去向"变得不可预测；用户应当在计划阶段就看到冲突并决定。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from evoagent.projects.editing import EditConflictError, move_path
from evoagent.projects.schema import resolve_inside_root
from evoagent.tools.base import ToolExecutionError, ToolPermissionError

MAX_ORGANIZE_ITEMS = 200


@dataclass(frozen=True, slots=True)
class MoveRule:
    """一条"把 source 移到 destination"的规则。"""

    source: str
    destination: str


@dataclass(frozen=True, slots=True)
class PlannedMove:
    source: str
    destination: str
    status: str
    reason: str = ""


@dataclass(slots=True)
class MovePlan:
    items: list[PlannedMove] = field(default_factory=list)

    @property
    def ready(self) -> list[PlannedMove]:
        return [item for item in self.items if item.status == "ready"]

    @property
    def conflicts(self) -> list[PlannedMove]:
        return [item for item in self.items if item.status == "conflict"]

    @property
    def skipped(self) -> list[PlannedMove]:
        return [item for item in self.items if item.status == "skipped"]

    def as_dict(self) -> dict[str, object]:
        return {
            "total": len(self.items),
            "ready": len(self.ready),
            "conflicts": len(self.conflicts),
            "skipped": len(self.skipped),
            "items": [
                {
                    "source": item.source,
                    "destination": item.destination,
                    "status": item.status,
                    "reason": item.reason,
                }
                for item in self.items
            ],
        }


def _display(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:  # pragma: no cover - 调用方已保证在根内
        return path.name


def plan_moves(root: Path, rules: list[MoveRule]) -> MovePlan:
    """生成计划与冲突清单，**不碰磁盘**。"""

    if not rules:
        raise ToolExecutionError("至少需要一条整理规则")
    if len(rules) > MAX_ORGANIZE_ITEMS:
        raise ToolExecutionError(f"一次整理的条目过多（上限 {MAX_ORGANIZE_ITEMS}）")

    plan = MovePlan()
    seen_sources: set[str] = set()
    seen_targets: set[str] = set()
    for rule in rules:
        source, destination = rule.source.strip(), rule.destination.strip()
        if not source or not destination:
            raise ToolExecutionError("整理规则的源与目标都不能为空")
        try:
            source_path, source_display = _resolve_existing(root, source)
        except (ToolPermissionError, ToolExecutionError) as error:
            plan.items.append(
                PlannedMove(
                    source=source, destination=destination, status="skipped", reason=str(error)
                )
            )
            continue
        try:
            target_path, target_display = _resolve_target(root, destination)
        except (ToolPermissionError, ToolExecutionError) as error:
            plan.items.append(
                PlannedMove(
                    source=source_display,
                    destination=destination,
                    status="skipped",
                    reason=str(error),
                )
            )
            continue

        if source_display in seen_sources:
            plan.items.append(
                PlannedMove(
                    source=source_display,
                    destination=target_display,
                    status="conflict",
                    reason="同一个源在同一批整理里出现多次",
                )
            )
            continue
        seen_sources.add(source_display)

        if source_display == target_display:
            plan.items.append(
                PlannedMove(
                    source=source_display,
                    destination=target_display,
                    status="skipped",
                    reason="源与目标相同，无需移动",
                )
            )
            continue
        if target_display in seen_targets:
            plan.items.append(
                PlannedMove(
                    source=source_display,
                    destination=target_display,
                    status="conflict",
                    reason="同一批整理里有两个源指向同一目标",
                )
            )
            continue
        seen_targets.add(target_display)

        if target_path.exists():
            plan.items.append(
                PlannedMove(
                    source=source_display,
                    destination=target_display,
                    status="conflict",
                    reason="目标已存在；整理不会覆盖已有文件，请先改名或换目录",
                )
            )
            continue
        if not target_path.parent.is_dir():
            plan.items.append(
                PlannedMove(
                    source=source_display,
                    destination=target_display,
                    status="skipped",
                    reason="目标目录不存在，请先创建目录",
                )
            )
            continue

        plan.items.append(
            PlannedMove(source=source_display, destination=target_display, status="ready")
        )
    return plan


def apply_plan(root: Path, plan: MovePlan) -> dict[str, object]:
    """执行计划里 ready 的条目；中途失败则逆序回滚已完成的部分。"""

    executed: list[PlannedMove] = []
    rolled_back: list[str] = []
    failure: str | None = None
    try:
        for item in plan.ready:
            move_path(root, item.source, item.destination)
            executed.append(item)
    except (EditConflictError, ToolExecutionError, ToolPermissionError) as error:
        failure = str(error)
        for item in reversed(executed):
            try:
                move_path(root, item.destination, item.source)
                rolled_back.append(item.source)
            except (EditConflictError, ToolExecutionError, ToolPermissionError):  # pragma: no cover
                pass

    return {
        "executed": [{"source": item.source, "destination": item.destination} for item in executed],
        "rolled_back": rolled_back,
        "failed": failure,
        "conflicts": [
            {"source": item.source, "destination": item.destination, "reason": item.reason}
            for item in plan.conflicts
        ],
        "skipped": [
            {"source": item.source, "destination": item.destination, "reason": item.reason}
            for item in plan.skipped
        ],
    }


def _resolve_existing(root: Path, requested: str) -> tuple[Path, str]:
    _lexical, physical = resolve_inside_root(root, requested)
    if physical.is_symlink():
        raise ToolPermissionError(f"{requested} 是符号链接，拒绝整理")
    if not physical.exists():
        raise ToolExecutionError(f"源不存在：{requested}")
    return physical, _display(root, physical)


def _resolve_target(root: Path, requested: str) -> tuple[Path, str]:
    lexical, physical = resolve_inside_root(root, requested)
    target = physical if physical.exists() else lexical
    return target, _display(root, target)
