"""阶段三运行模式与可重现实验配置。"""

import hashlib
import json
from enum import StrEnum
from typing import Any, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class RunMode(StrEnum):
    """一次 Run 使用 Skill 的方式。"""

    BASELINE = "baseline"
    RETRIEVAL = "retrieval"
    PINNED_SKILL = "pinned_skill"


class RunConfigSnapshot(BaseModel):
    """只包含非敏感、可用于复现实验的运行配置。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    provider: str = Field(min_length=1, max_length=64)
    model: str = Field(min_length=1, max_length=256)
    system_prompt_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    tool_manifest_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    policy_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    max_iterations: int = Field(ge=1)
    max_total_tokens: int = Field(ge=1)
    max_repeated_tool_calls: int = Field(default=3, ge=1)
    max_tool_result_chars: int = Field(default=20_000, ge=1)
    temperature: float | None = Field(default=None, ge=0, le=2)
    max_output_tokens: int | None = Field(default=None, ge=1)
    model_timeout_seconds: float = Field(gt=0)
    task_timeout_seconds: float = Field(gt=0)
    tool_timeout_seconds: float = Field(gt=0)
    code_version: str = Field(min_length=1, max_length=128)
    skill_retrieval_top_k: int = Field(default=1, ge=0, le=3)
    skill_retrieval_min_score: float = Field(default=0.1, ge=0)
    run_mode: RunMode
    skill_version_id: UUID | None = None
    skill_content_hash: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")
    skill_context_hash: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_skill_fields(self) -> Self:
        if self.run_mode is RunMode.PINNED_SKILL and self.skill_version_id is None:
            raise ValueError("pinned skill mode requires skill_version_id")
        if (self.skill_version_id is None) != (self.skill_content_hash is None):
            raise ValueError("skill id and content hash must be provided together")
        if (self.skill_version_id is None) != (self.skill_context_hash is None):
            raise ValueError("selected skill and complete context hash must be provided together")
        if self.run_mode is RunMode.BASELINE and self.skill_version_id is not None:
            raise ValueError("baseline mode cannot include a skill")
        return self

    def canonical_dict(self, *, comparison: bool = False) -> dict[str, Any]:
        value = self.model_dump(mode="json")
        if comparison:
            value["run_mode"] = "experiment_variable"
            value["skill_version_id"] = None
            value["skill_content_hash"] = None
            value["skill_context_hash"] = None
        return value

    def content_hash(self, *, comparison: bool = False) -> str:
        encoded = json.dumps(
            self.canonical_dict(comparison=comparison),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        return "sha256:" + hashlib.sha256(encoded).hexdigest()

    def comparable_with(self, other: "RunConfigSnapshot") -> bool:
        """判断两个 Run 是否只有 Skill 实验变量不同。"""

        return self.content_hash(comparison=True) == other.content_hash(comparison=True)


def sha256_text(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode()).hexdigest()
