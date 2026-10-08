"""模型所见工具视图：把宿主生成的元数据与安全正文一起交给模型。

为什么需要（改造方案 §2.3）：模型此前拿到的工具输出经过了脱敏，却**没有任何标记**
说明"这份视图是有损的"。于是它可能把占位符当成原文写回，或反复用缩小行范围去猜
被隐藏的值。本模块负责：

- 统一投影：短输出、长输出、归档分页、复用结果与失败信息都带同样的视图元数据；
- 渲染：把元数据（含固定提示）与正文拼成模型**真正收到**的 content；
- 解析：`split_view()` 让需要按行理解正文的调用方（例如工具指纹）跳过头部。

头部预算：元数据先占预算，正文再截断；预算连完整头部都放不下时抛明确错误，
**不剪掉提示、也不回退原文**（那等于静默解除保护）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from evoagent.core.models import ToolResult, ToolViewMetadata
from evoagent.tools.base import ToolExecutionError

#: 视图头的 JSON 键；`split_view` 靠它识别头部，别处不要硬编码。
VIEW_HEADER_KEY = "view"
#: 头部预算：正文截断前先留出这么多字符给元数据与提示。
VIEW_HEADER_RESERVE = 256

VIEW_HINT = (
    "此视图已隐藏敏感片段，不可将占位符当原文写回；缩小行范围不会解除隐藏，"
    "涉及隐藏片段请停止并请求用户本地处理"
)


@dataclass(frozen=True, slots=True)
class PreparedToolOutput:
    """一次投影的结果：安全正文 + 视图元数据 + 可选的归档引用。"""

    content: str
    view_metadata: ToolViewMetadata
    artifact_ref: dict[str, Any] | None = None


def unknown_view(*, truncated: bool = False) -> ToolViewMetadata:
    """旧记录或未经投影的结果：明确标成 unknown，不假装可信。"""

    return ToolViewMetadata(source_view="unknown", truncated=truncated)


def render_model_view(result: ToolResult, *, max_chars: int | None = None) -> str:
    """把结果渲染成模型实际收到的内容：一行 JSON 头 + 正文。

    `max_chars` 给出时，头部必须能完整放下，否则抛 `view_header_budget_exceeded`。
    """

    metadata = result.view_metadata or unknown_view()
    header = _render_header(metadata)
    if max_chars is not None and len(header) + 1 >= max_chars:
        raise ToolExecutionError(
            "view header does not fit the output budget",
            code="view_header_budget_exceeded",
        )
    return f"{header}\n{result.content}"


def split_view(content: str) -> tuple[ToolViewMetadata | None, str]:
    """拆出视图头与正文；没有头部时返回 `(None, 原内容)`。

    供需要按行理解正文的调用方使用（例如工具指纹里的归档引用归一化）——
    它们必须跳过头部，否则头部会被误当成正文的第一行。
    """

    first, separator, rest = content.partition("\n")
    if not separator or not first.startswith("{"):
        return None, content
    try:
        parsed = json.loads(first)
    except ValueError:
        return None, content
    if not isinstance(parsed, dict) or VIEW_HEADER_KEY not in parsed:
        return None, content
    raw = parsed[VIEW_HEADER_KEY]
    if not isinstance(raw, dict):
        return None, content
    try:
        return ToolViewMetadata.model_validate(raw), rest
    except ValueError:
        return None, content


def _render_header(metadata: ToolViewMetadata) -> str:
    payload: dict[str, Any] = {
        VIEW_HEADER_KEY: metadata.model_dump(mode="json"),
    }
    if metadata.source_view == "redacted":
        # 提示只在确实隐藏过内容时出现，避免噪声；unknown/verbatim 只靠元数据表达。
        payload["hint"] = VIEW_HINT
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
