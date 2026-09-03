"""从 ``EVOAGENT_*`` 环境变量加载的类型化运行配置。"""

from enum import StrEnum
from pathlib import Path
from typing import Self

from pydantic import AnyHttpUrl, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ProviderName(StrEnum):
    """可以通过配置选择的模型服务实现。"""

    MOCK = "mock"
    OPENAI_COMPATIBLE = "openai_compatible"


class LogLevel(StrEnum):
    """应用程序支持的日志级别。"""

    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"


class Settings(BaseSettings):
    """单个 EvoAgent 进程经过校验的配置。

    Mock 模式特意不要求任何密钥，以保证测试和首次本地运行是确定的。
    只有选择 OpenAI 兼容适配器时，才会统一检查真实模型服务所需的字段。
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
        """仅在使用真实模型服务时要求提供连接信息。"""

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
