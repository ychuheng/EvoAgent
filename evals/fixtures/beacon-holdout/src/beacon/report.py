"""Report rendering for the beacon pipeline."""

from pathlib import Path


def render(rows: list[tuple[str, int]]) -> str:
    lines = []
    for name, count in rows:
        if name:
            lines.append(f"{name}: {count}")
        else:
            # 已知缺陷：空名称也输出一行空字符串，报告里多出一个空行。
            lines.append("")
    return "\n".join(lines)


def write_report(path: Path, rows: list[tuple[str, int]]) -> None:
    path.write_text(render(rows) + "\n", encoding="utf-8")
