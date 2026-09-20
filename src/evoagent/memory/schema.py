"""API 和提取器共享的窄输入契约。"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class MemoryProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_message_id: UUID
    fact_key: str = Field(min_length=1, max_length=128, pattern=r"^[\w.:-]+$")
    content: str = Field(min_length=1, max_length=4000)
    kind: Literal["preference", "fact", "constraint"] = "preference"
    scope: Literal["session", "workspace"] = "session"
    expires_at: datetime | None = None


class MemoryDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["confirm", "reject", "revoke", "erase"]
    expected_lock_version: int = Field(ge=0)


class MemoryError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)
