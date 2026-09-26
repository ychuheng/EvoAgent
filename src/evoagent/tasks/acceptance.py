"""Optional, deterministic acceptance checks for ordinary tasks.

These checks prove only the explicit conditions supplied with a task. They do not
judge open-ended answer quality or infer intent from model prose.
"""

import asyncio
import hashlib
import re
from pathlib import Path
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from evoagent.db.models import ToolCallRecord, ToolCallStatus


class RequiredFile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str = Field(min_length=1, max_length=1024)
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @field_validator("path")
    @classmethod
    def safe_relative_path(cls, value: str) -> str:
        normalized = value.replace("\\", "/")
        parts = normalized.split("/")
        if (
            normalized.startswith("/")
            or re.match(r"^[A-Za-z]:", normalized)
            or ":" in normalized
            or any(part in {"", ".", ".."} for part in parts)
            or "\x00" in normalized
        ):
            raise ValueError("file path must be a plain relative path")
        return normalized


class AcceptanceSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    answer_contains: list[str] = Field(default_factory=list, max_length=10)
    required_tools: list[str] = Field(default_factory=list, max_length=10)
    required_files: list[RequiredFile] = Field(default_factory=list, max_length=10)

    @field_validator("answer_contains")
    @classmethod
    def validate_texts(cls, values: list[str]) -> list[str]:
        normalized = [value.strip() for value in values]
        if any(not value or len(value) > 200 for value in normalized):
            raise ValueError("each required answer text must be 1 to 200 characters")
        return normalized

    @field_validator("required_tools")
    @classmethod
    def validate_tools(cls, values: list[str]) -> list[str]:
        normalized = [value.strip() for value in values]
        if any(not re.fullmatch(r"[A-Za-z0-9_.:-]{1,64}", value) for value in normalized):
            raise ValueError("invalid required tool name")
        return normalized

    @model_validator(mode="after")
    def require_condition(self) -> "AcceptanceSpec":
        if not (self.answer_contains or self.required_tools or self.required_files):
            raise ValueError("acceptance must contain at least one condition")
        return self


class AcceptanceResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    passed: bool
    checks: tuple[dict[str, str | bool], ...]


async def check_acceptance(
    spec: AcceptanceSpec,
    *,
    answer: str,
    run_id: UUID,
    session_factory: async_sessionmaker[AsyncSession],
    artifact_root: Path,
) -> AcceptanceResult:
    checks: list[dict[str, str | bool]] = []
    folded_answer = answer.casefold()
    for index, text in enumerate(spec.answer_contains, start=1):
        checks.append(
            {
                "type": "answer_contains",
                "target": str(index),
                "passed": text.casefold() in folded_answer,
            }
        )

    if spec.required_tools:
        async with session_factory() as session:
            successful_tools = set(
                await session.scalars(
                    select(ToolCallRecord.tool_name).where(
                        ToolCallRecord.run_id == run_id,
                        ToolCallRecord.status == ToolCallStatus.SUCCEEDED,
                    )
                )
            )
        for name in spec.required_tools:
            checks.append(
                {"type": "tool_succeeded", "target": name, "passed": name in successful_tools}
            )

    root = (artifact_root / str(run_id)).resolve(strict=False)
    for item in spec.required_files:
        target = (root / item.path).resolve(strict=False)
        valid = target.is_relative_to(root) and target.is_file()
        if valid:
            valid = target.stat().st_size <= 16_000_000
        if valid and item.sha256 is not None:
            valid = await asyncio.to_thread(_matches_hash, target, item.sha256)
        checks.append(
            {
                "type": "file_exists" if item.sha256 is None else "file_sha256",
                "target": item.path,
                "passed": valid,
            }
        )

    return AcceptanceResult(
        passed=all(bool(item["passed"]) for item in checks), checks=tuple(checks)
    )


def _matches_hash(path: Path, expected: str) -> bool:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(65536), b""):
            digest.update(block)
    return digest.hexdigest() == expected
