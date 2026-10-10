"""Explicit human evidence contract, not interpretation of feedback prose."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

OBSERVABLE_TERMINAL_STATES = frozenset(
    {
        "completed",
        "failed",
        "cancelled",
        "timeout",
        "limit_reached",
        "authorization_revoked",
        "input_changed",
    }
)


class ObservationArtifact(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    artifact_id: UUID
    content_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class HumanSkillObservation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    type: Literal["skill_observation"]
    schema_version: Literal[1]
    version_id: UUID
    criterion_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{1,63}$")
    outcome: Literal["verified_success", "verified_failure"]
    attribution: Literal["skill_related", "environment", "user_request", "uncertain"]
    associated_steps: tuple[str, ...] = Field(min_length=1, max_length=20)
    artifacts: tuple[ObservationArtifact, ...] = Field(min_length=1, max_length=20)
