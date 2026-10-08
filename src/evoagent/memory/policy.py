"""内容进入持久化记忆之前的保守门禁。

自 S0a 起，**敏感内容脱敏**的实现移入 `evoagent.privacy.redaction`；本模块保留
原函数名作为兼容包装，避免一次性改动全部调用点。本模块继续负责**内容门禁**
（不安全内容与一次性指令），它不是脱敏规则，两者的判定互不替代。

等价性由 `tests/unit/test_redaction_compatibility.py` 对照冻结 oracle 保证。
"""

import re

from evoagent.memory.schema import MemoryError
from evoagent.privacy.redaction import redact_text as _redact_text
from evoagent.privacy.redaction import redact_value as _redact_value

UNSAFE = re.compile(
    r"sk-[\w-]{8,}|Bearer\s+\S+|-----BEGIN .*PRIVATE KEY|"
    r"(?:password|api[_ -]?key|密码|密钥)\s*[:=：]|"
    r"ignore (?:all |the )?(?:previous|system)|忽略.{0,12}(?:指令|规则)|"
    r"(?:system|developer)\s*(?:prompt|message)|绕过.{0,8}(?:权限|审批)",
    re.I,
)
ONE_SHOT = re.compile(r"这次|本次|仅此次|this time|for this (?:task|run) only", re.I)


def validate_content(content: str, *, persistent: bool = True) -> None:
    if UNSAFE.search(content):
        raise MemoryError("unsafe_memory_content")
    if persistent and ONE_SHOT.search(content):
        raise MemoryError("one_shot_instruction")


def redact(content: str) -> str:
    """兼容包装；规则集合与替换顺序见 `privacy.redaction.redact_text`。"""

    return _redact_text(content)


def redact_value(value):
    """兼容包装；按字段名与文本规则递归脱敏，行为与抽取前一致。"""

    return _redact_value(value)
