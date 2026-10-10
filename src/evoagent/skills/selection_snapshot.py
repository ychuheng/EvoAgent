"""Typed metadata for actual injection, distinct from an experiment target."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from evoagent.skills.canonical import content_hash


class SkillSelectionScope(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    workspace_id: UUID
    project_id: UUID | None = None


class SkillSelectionSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    version_id: UUID
    content_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    rendered_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    origin: Literal["formal", "trial", "pinned"]
    scope: SkillSelectionScope
    trial_id: UUID | None = None

    @model_validator(mode="after")
    def trial_identity(self):
        if (self.origin == "trial") != (self.trial_id is not None):
            raise ValueError("only trial injection requires a trial identity")
        return self


def selection_hash(*, selector_version, renderer_version, selections):
    return content_hash(
        {
            "selector_version": selector_version,
            "renderer_version": renderer_version,
            "selected_skills": [item.model_dump(mode="json") for item in selections],
        }
    )
