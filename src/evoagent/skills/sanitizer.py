"""把合格 Trace 转成最小化、可冻结的候选生成资料。"""

import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from evoagent.skills.canonical import content_hash

_SENSITIVE_KEY = re.compile(
    r"(?i)(api[_-]?key|authorization|password|secret|"
    r"(?:access|refresh|auth)[_-]?token|^token$|cookie)$"
)
_PRIVATE_KEY = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")
_PROMPT_INJECTION = re.compile(
    r"(?i)(ignore (all |the )?(previous|system) instructions|"
    r"忽略.{0,12}(之前|系统).{0,8}(指令|提示))"
)
_WINDOWS_PATH = re.compile(r"(?i)[a-z]:[\\/][^\s\"']+")
_UNIX_PATH = re.compile(r"(?<![\w:/])/(?:home|tmp|var|users|opt)/[^\s\"']+")
_CREDENTIAL_VALUE = re.compile(r"(?i)(?:bearer\s+|sk-)[a-z0-9_-]{20,}")
_REASONING_KEY = re.compile(r"(?i)(chain[_-]?of[_-]?thought|reasoning|hidden[_-]?thoughts)")
_EPHEMERAL_KEYS = {
    "id",
    "run_id",
    "eval_run_id",
    "task_id",
    "provider_call_id",
    "tool_call_id",
    "created_at",
    "updated_at",
    "started_at",
    "ended_at",
    "timestamp",
    "port",
}


@dataclass(frozen=True, slots=True)
class SanitizerFinding:
    path: str
    kind: str
    message: str
    blocking: bool = True


@dataclass(frozen=True, slots=True)
class SanitizedTrace:
    payload: dict[str, Any]
    source_trace_hash: str
    findings: tuple[SanitizerFinding, ...]


class TraceSanitizationError(ValueError):
    def __init__(self, findings: tuple[SanitizerFinding, ...]) -> None:
        summary = ", ".join(f"{item.path}:{item.kind}" for item in findings)
        super().__init__(f"trace contains data that cannot safely enter extraction: {summary}")
        self.findings = findings


class TraceSanitizer:
    def __init__(self, workspace: Path | None = None) -> None:
        self._workspace = workspace.resolve(strict=False) if workspace is not None else None

    def sanitize(self, payload: dict[str, Any]) -> SanitizedTrace:
        findings: list[SanitizerFinding] = []
        cleaned = self._visit(payload, "$", findings)
        blocking = tuple(item for item in findings if item.blocking)
        if blocking:
            raise TraceSanitizationError(blocking)
        return SanitizedTrace(cleaned, content_hash(cleaned), tuple(findings))

    def _visit(self, value: Any, path: str, findings: list[SanitizerFinding]) -> Any:
        if isinstance(value, dict):
            result: dict[str, Any] = {}
            for key, item in value.items():
                child_path = f"{path}.{key}"
                if str(key) in _EPHEMERAL_KEYS:
                    findings.append(
                        SanitizerFinding(child_path, "ephemeral", "temporary value removed", False)
                    )
                    continue
                if _REASONING_KEY.search(str(key)):
                    findings.append(
                        SanitizerFinding(
                            child_path, "hidden_reasoning", "hidden reasoning removed", False
                        )
                    )
                    continue
                if _SENSITIVE_KEY.search(str(key)):
                    findings.append(
                        SanitizerFinding(child_path, "sensitive_key", "sensitive field detected")
                    )
                    continue
                result[str(key)] = self._visit(item, child_path, findings)
            return result
        if isinstance(value, list | tuple):
            return [
                self._visit(item, f"{path}[{index}]", findings) for index, item in enumerate(value)
            ]
        if not isinstance(value, str):
            return value
        if _PRIVATE_KEY.search(value):
            findings.append(SanitizerFinding(path, "private_key", "private key material detected"))
        if _PROMPT_INJECTION.search(value):
            findings.append(
                SanitizerFinding(
                    path, "prompt_injection", "instruction-like source content detected"
                )
            )
        contains_path = _WINDOWS_PATH.search(value) or _UNIX_PATH.search(value)
        if _CREDENTIAL_VALUE.search(value) or (
            not contains_path and self._looks_high_entropy(value)
        ):
            findings.append(SanitizerFinding(path, "credential", "credential-like value detected"))
        return self._replace_paths(value, path, findings)

    @staticmethod
    def _looks_high_entropy(value: str) -> bool:
        candidate = value.strip()
        if not 32 <= len(candidate) <= 160 or any(character.isspace() for character in candidate):
            return False
        if re.fullmatch(r"[A-Za-z0-9_+/=.-]+", candidate) is None:
            return False
        if candidate.startswith(("sha256:", "http://", "https://", "${workspace}")):
            return False
        counts = Counter(candidate)
        entropy = -sum(
            (count / len(candidate)) * math.log2(count / len(candidate))
            for count in counts.values()
        )
        return entropy >= 4.3

    def _replace_paths(self, value: str, path: str, findings: list[SanitizerFinding]) -> str:
        result = value
        for pattern in (_WINDOWS_PATH, _UNIX_PATH):
            for match in reversed(tuple(pattern.finditer(result))):
                raw = match.group(0)
                if self._workspace is not None:
                    try:
                        relative = Path(raw).resolve(strict=False).relative_to(self._workspace)
                    except (OSError, ValueError):
                        relative = None
                    if relative is not None:
                        replacement = "${workspace}/" + relative.as_posix()
                        result = result[: match.start()] + replacement + result[match.end() :]
                        findings.append(
                            SanitizerFinding(
                                path, "workspace_path", "workspace path replaced", False
                            )
                        )
                        continue
                findings.append(
                    SanitizerFinding(path, "absolute_path", "external absolute path detected")
                )
        return result
