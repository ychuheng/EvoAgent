"""Typed runtime configuration loaded from ``EVOAGENT_*`` environment variables."""

from enum import StrEnum
from pathlib import Path
from typing import Self

from pydantic import AnyHttpUrl, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ProviderName(StrEnum):
    """Provider implementations selectable through configuration."""

    MOCK = "mock"
    OPENAI_COMPATIBLE = "openai_compatible"


class LogLevel(StrEnum):
    """Supported application log levels."""

    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"


class Settings(BaseSettings):
    """Validated settings for one EvoAgent process.

    Mock mode deliberately has no secret requirements so that tests and the first
    local run are deterministic. Real-provider fields are checked together only
    when the OpenAI-compatible adapter is selected.
    """

    model_config = SettingsConfigDict(
        env_prefix="EVOAGENT_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    provider: ProviderName = ProviderName.MOCK
    api_key: SecretStr | None = None
    base_url: AnyHttpUrl | None = None
    model: str | None = None

    max_iterations: int = Field(default=8, ge=1, le=100)
    model_timeout_seconds: float = Field(default=60.0, gt=0, le=600)
    task_timeout_seconds: float = Field(default=300.0, gt=0, le=86_400)
    tool_timeout_seconds: float = Field(default=30.0, gt=0, le=3_600)
    max_total_tokens: int = Field(default=32_000, ge=1)
    max_repeated_tool_calls: int = Field(default=3, ge=1, le=20)
    max_tool_result_chars: int = Field(default=20_000, ge=1, le=1_000_000)
    workspace: Path = Path("./workspace")
    log_level: LogLevel = LogLevel.INFO

    @model_validator(mode="after")
    def validate_provider_requirements(self) -> Self:
        """Require connection details only for the real provider."""

        if self.provider is ProviderName.OPENAI_COMPATIBLE:
            missing: list[str] = []
            if self.api_key is None or not self.api_key.get_secret_value().strip():
                missing.append("EVOAGENT_API_KEY")
            if self.base_url is None:
                missing.append("EVOAGENT_BASE_URL")
            if self.model is None or not self.model.strip():
                missing.append("EVOAGENT_MODEL")
            if missing:
                fields = ", ".join(missing)
                raise ValueError(f"openai_compatible provider requires: {fields}")

        self.workspace = self.workspace.expanduser().resolve(strict=False)
        return self
