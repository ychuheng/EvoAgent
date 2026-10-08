"""敏感内容检测与文本脱敏的唯一实现。

本模块是 S0a 的**行为等价抽取**：规则集合、替换顺序与重构前的
`memory.policy.redact/redact_value` 完全一致，不新增规则、不改变输出字节。
规则扩容（凭据 URL/DSN、JWT 等）属于 S0b，必须同时升 `POLICY_VERSION`
并登记到 `tests/fixtures/redaction/expected_changes.json`。

刻意**不**包含的内容（避免把两种不同职责混成一个不变量）：

- 结构脱敏（按字段名判断）在各调用点各自保留：`core.events.sanitize_payload`
  使用自己的敏感键集合（含 chain_of_thought/reasoning_content），
  `redact_value` 使用本模块的键规则。两者集合不同，S0a 不合并且不改动。
- `skills/sanitizer.py::TraceSanitizer` 是另一条路径（阻塞式 + 高熵启发式 +
  路径替换），S0a 不接线。
- 内容门禁（一次性指令、提示注入）仍属 `memory.policy.validate_content`，
  它不是脱敏规则。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

#: 只增的规则包版本。规则扩容或替换语义必须升版，否则旧检查结果会被误复用。
POLICY_VERSION = 1

REDACTED = "[REDACTED]"
PRIVATE_KEY_PLACEHOLDER = "[REDACTED PRIVATE KEY]"

_KEY_PATTERN = re.compile(r"password|api[_-]?key|authorization|密钥|密码", re.IGNORECASE)

#: （类别, 正则, 替换文本）。**元组顺序就是替换顺序**，改动会改变输出字节。
_SENSITIVE_TEXT_RULES: tuple[tuple[str, re.Pattern[str], str], ...] = (
    (
        "private_key",
        re.compile(
            r"-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----",
            re.DOTALL,
        ),
        PRIVATE_KEY_PLACEHOLDER,
    ),
    (
        "credential",
        re.compile(
            r"sk-[\w-]{8,}|Bearer\s+\S+|(?:password|api[_ -]?key|密码|密钥)\s*[:=：]\s*\S+",
            re.IGNORECASE,
        ),
        REDACTED,
    ),
)


@dataclass(frozen=True, slots=True)
class RedactionResult:
    """一次文本脱敏的结果与依据。"""

    text: str
    categories: tuple[str, ...]
    policy_version: int = POLICY_VERSION

    @property
    def redacted(self) -> bool:
        return bool(self.categories)


def detect_sensitive(text: str) -> tuple[str, ...]:
    """返回在**原始文本**上命中的类别，按规则顺序去重。"""

    seen: list[str] = []
    for name, pattern, _ in _SENSITIVE_TEXT_RULES:
        if pattern.search(text) and name not in seen:
            seen.append(name)
    return tuple(seen)


def redact_text(text: str) -> str:
    """按固定顺序替换敏感片段；不改变其它字节。"""

    result = text
    for _, pattern, replacement in _SENSITIVE_TEXT_RULES:
        result = pattern.sub(replacement, result)
    return result


def redact_text_result(text: str) -> RedactionResult:
    """检测与脱敏一次完成，供需要记录依据（而非只改文本）的调用方使用。"""

    return RedactionResult(redact_text(text), detect_sensitive(text))


def redact_value(value: Any) -> Any:
    """递归按字段名与文本规则脱敏；不改动非字符串标量。

    刻意保持与重构前 `memory.policy.redact_value` 完全一致，包括**不对键做类型
    转换**：非字符串键会让 `_KEY_PATTERN.search` 抛 `TypeError`，这与重构前相同。
    把它改成 `str(key)` 属于行为变更，须另行登记，不能混进等价抽取。
    """

    if isinstance(value, dict):
        return {
            key: REDACTED if _KEY_PATTERN.search(key) else redact_value(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_value(item) for item in value]
    return redact_text(value) if isinstance(value, str) else value
