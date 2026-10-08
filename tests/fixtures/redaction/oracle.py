"""等价对照用的**冻结 oracle**：重构前的敏感内容脱敏实现。

本文件是 `memory/policy.py::redact/redact_value` 在抽取前的逐字副本
（来源提交 `583059a`，2026-10-08 复制）。它只服务于
`tests/unit/test_redaction_compatibility.py`：

- **禁止**修改本文件去迁就新实现。新实现必须向它对齐。
- 它不 import 任何 `evoagent` 代码，因此不受被重构模块的影响。
- 若将来确需改变行为（S0b 的规则扩容），做法是**登记**差异到
  `tests/fixtures/redaction/expected_changes.json`，而不是改这里。
"""

from __future__ import annotations

import re
from typing import Any

ORACLE_SOURCE_COMMIT = "583059a"
ORACLE_POLICY_VERSION = 1


def redact(content: str) -> str:
    # 完整输出只指脱敏后的完整内容；不保留可反向恢复的秘密副本。
    content = re.sub(
        r"-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----",
        "[REDACTED PRIVATE KEY]",
        content,
        flags=re.DOTALL,
    )
    return re.sub(
        r"sk-[\w-]{8,}|Bearer\s+\S+|(?:password|api[_ -]?key|密码|密钥)\s*[:=：]\s*\S+",
        "[REDACTED]",
        content,
        flags=re.IGNORECASE,
    )


def redact_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "[REDACTED]"
            if re.search(r"password|api[_-]?key|authorization|密钥|密码", key, re.I)
            else redact_value(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_value(item) for item in value]
    return redact(value) if isinstance(value, str) else value
