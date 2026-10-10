"""阶段三运行模式与可重现实验配置。"""

import hashlib
import json
from enum import StrEnum
from typing import Any, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_serializer, model_validator

from evoagent.skills.selection_snapshot import SkillSelectionSnapshot, selection_hash


class RunMode(StrEnum):
    """一次 Run 使用 Skill 的方式。"""

    BASELINE = "baseline"
    RETRIEVAL = "retrieval"
    PINNED_SKILL = "pinned_skill"


class RunConfigSnapshot(BaseModel):
    """只包含非敏感、可用于复现实验的运行配置。"""

    model_config = ConfigDict(frozen=True, extra="forbid")
    schema_version: int = Field(default=1, ge=1, le=3)
    summarizer: str | None = None
    selected_skills: list[dict[str, str]] | tuple[SkillSelectionSnapshot, ...] | None = None
    selector_version: Literal["skill-selector-v1"] | None = None
    renderer_version: Literal[2] | None = None
    skill_selection_hash: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")
    under_test_skill_version_id: UUID | None = None
    retrieval: dict[str, Any] | None = None
    skill_renderer_version: Literal[1, 2] | None = None
    progress_write_mode: Literal["batched_v1"] | None = None

    provider: str = Field(min_length=1, max_length=64)
    provider_thinking_mode: Literal["disabled"] | None = None
    model: str = Field(min_length=1, max_length=256)
    system_prompt_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    tool_manifest_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    policy_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    max_iterations: int = Field(ge=1)
    max_total_tokens: int = Field(ge=1)
    max_repeated_tool_calls: int = Field(default=3, ge=1)
    max_tool_result_chars: int = Field(default=20_000, ge=1)
    temperature: float | None = Field(default=None, ge=0, le=2)
    context_policy: dict[str, Any] | None = None
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
        if self.schema_version < 3 and any(
            value is not None
            for value in (
                self.selector_version,
                self.renderer_version,
                self.skill_selection_hash,
                self.under_test_skill_version_id,
            )
        ):
            raise ValueError("legacy config cannot carry v3 selection metadata")
        if self.schema_version < 3 and isinstance(self.selected_skills, tuple):
            raise ValueError("legacy config requires legacy selections")
        if self.schema_version == 3:
            self._validate_v3_selection()
        if (
            self.schema_version < 3
            and self.run_mode is RunMode.PINNED_SKILL
            and self.skill_version_id is None
        ):
            raise ValueError("pinned skill mode requires skill_version_id")
        if (self.skill_version_id is None) != (self.skill_content_hash is None):
            raise ValueError("skill id and content hash must be provided together")
        if (self.skill_version_id is None) != (self.skill_context_hash is None):
            raise ValueError("selected skill and complete context hash must be provided together")
        if self.run_mode is RunMode.BASELINE and self.skill_version_id is not None:
            raise ValueError("baseline mode cannot include a skill")
        return self

    def _validate_v3_selection(self):
        if (
            self.selector_version is None
            or self.renderer_version is None
            or self.skill_renderer_version != self.renderer_version
            or self.selected_skills is None
            or self.skill_selection_hash is None
            or self.skill_retrieval_top_k > 1
        ):
            raise ValueError("v3 requires frozen selector, renderer and actual selections")
        try:
            selections = tuple(
                SkillSelectionSnapshot.model_validate(item) for item in self.selected_skills
            )
        except ValueError:
            raise ValueError("v3 requires typed actual selections") from None
        if len(selections) > 1:
            raise ValueError("v3 supports at most one actual skill")
        if self.skill_selection_hash != selection_hash(
            selector_version=self.selector_version,
            renderer_version=self.renderer_version,
            selections=selections,
        ):
            raise ValueError("v3 selection hash mismatch")
        if selections:
            first = selections[0]
            if (
                first.version_id != self.skill_version_id
                or first.content_hash != self.skill_content_hash
                or first.rendered_hash != self.skill_context_hash
            ):
                raise ValueError("v3 actual selection and rendered identity disagree")
            if self.run_mode is RunMode.PINNED_SKILL:
                if first.origin != "pinned" or first.version_id != self.under_test_skill_version_id:
                    raise ValueError("pinned injection must match the experiment target")
            elif first.origin == "pinned":
                raise ValueError("ordinary runs cannot inject experimental pinned skills")
        elif self.skill_version_id is not None:
            raise ValueError("empty selection cannot claim actual injection")
        if self.run_mode is RunMode.PINNED_SKILL:
            if self.under_test_skill_version_id is None:
                raise ValueError("pinned experiment requires a target even when not applied")
        elif self.under_test_skill_version_id is not None:
            raise ValueError("only pinned experiments may carry an experiment target")
        object.__setattr__(self, "selected_skills", selections)

    @model_serializer(mode="wrap")
    def versioned_selection_body(self, handler):
        body = handler(self)
        if self.schema_version < 3:
            for field in (
                "selector_version",
                "renderer_version",
                "skill_selection_hash",
                "under_test_skill_version_id",
            ):
                body.pop(field, None)
        return body

    def canonical_dict(self, *, comparison: bool = False) -> dict[str, Any]:
        value = self.model_dump(mode="json")
        if self.skill_renderer_version is None:
            value.pop("skill_renderer_version")
        if self.progress_write_mode is None:
            value.pop("progress_write_mode")
        if self.provider_thinking_mode is None:
            value.pop("provider_thinking_mode")
        if self.schema_version == 1:
            value.pop("schema_version")
        if self.summarizer is None:
            value.pop("summarizer")
        if self.context_policy is None:
            value.pop("context_policy")
        if self.selected_skills is None:
            value.pop("selected_skills")
        if self.retrieval is None:
            value.pop("retrieval")
        if comparison:
            value["run_mode"] = "experiment_variable"
            value["skill_version_id"] = None
            value["skill_content_hash"] = None
            value["skill_context_hash"] = None
            if self.schema_version == 3:
                value["under_test_skill_version_id"] = None
                value["skill_selection_hash"] = None
            if "selected_skills" in value:
                value["selected_skills"] = None
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
