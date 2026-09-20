"""内容进入持久化记忆之前的保守门禁。"""

import re

from evoagent.memory.schema import MemoryError

UNSAFE = re.compile(
    r"sk-[\w-]{8,}|Bearer\s+\S+|-----BEGIN .*PRIVATE KEY|"
    r"(?:password|api[_ -]?key|密码|密钥)\s*[:=：]|"
    r"ignore (?:all |the )?(?:previous|system)|忽略.{0,12}(?:指令|规则)|"
    r"(?:system|developer)\s*(?:prompt|message)|绕过.{0,8}(?:权限|审批)",
    re.I,
)
ONE_SHOT = re.compile(r"这次|本次|仅此次|this time|for this (?:task|run) only", re.I)


def validate_content(content: str, *, persistent: bool = True):
    if UNSAFE.search(content):
        raise MemoryError("unsafe_memory_content")
    if persistent and ONE_SHOT.search(content):
        raise MemoryError("one_shot_instruction")


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


def redact_value(value):
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
