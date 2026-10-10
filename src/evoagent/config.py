"""从 ``EVOAGENT_*`` 环境变量加载的类型化运行配置。"""

import os
from enum import StrEnum
from pathlib import Path
from typing import Literal, Self
from uuid import UUID

from pydantic import AnyHttpUrl, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from evoagent.core.models import ToolRisk
from evoagent.mcp.schema import HTTPProfile, LaunchProfile
from evoagent.sandbox.schema import SandboxSpec


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
    provider_thinking_mode: Literal["disabled"] | None = None
    model_request_max_output_tokens: int | None = Field(default=None, ge=1)

    max_iterations: int = Field(default=8, ge=1, le=100)
    model_timeout_seconds: float = Field(default=60.0, gt=0, le=600)
    task_timeout_seconds: float = Field(default=300.0, gt=0, le=86_400)
    tool_timeout_seconds: float = Field(default=30.0, gt=0, le=3_600)
    context_policy: Literal["bounded", "legacy"] = "bounded"
    context_window_tokens: int = Field(default=32768, ge=1)
    max_output_tokens: int = Field(default=4096, ge=1)
    context_safety_margin: int = Field(default=1024, ge=0)
    context_strict: bool = False
    max_total_tokens: int = Field(default=32_000, ge=1)
    max_repeated_tool_calls: int = Field(default=3, ge=1, le=20)
    max_tool_result_chars: int = Field(default=20_000, ge=1, le=1_000_000)
    workspace: Path = Path("./workspace")
    log_level: LogLevel = LogLevel.INFO

    database_url: SecretStr = SecretStr(
        "postgresql+asyncpg://evoagent:evoagent@127.0.0.1:5432/evoagent"
    )
    api_host: str = "127.0.0.1"
    api_port: int = Field(default=8_000, ge=1, le=65_535)
    worker_id: str = Field(default="worker-local", min_length=1, max_length=128)
    worker_poll_seconds: float = Field(default=1.0, gt=0, le=60)
    worker_concurrency: int = Field(default=1, ge=1, le=64)
    runtime_event_batching_enabled: bool = False
    runtime_shared_notifications_enabled: bool = False
    runtime_recovery_scan_decoupled_enabled: bool = False
    runtime_maintenance_idle_backoff_enabled: bool = False
    runtime_heartbeat_status_merge_enabled: bool = False
    runtime_snapshot_deduplication_enabled: bool = False
    learning_enabled: bool = False
    personal_trial_enabled: bool = False
    worker_runtime_experiment_id: UUID | None = None
    worker_runtime_arm: Literal["control", "treatment"] | None = None
    redis_url: SecretStr | None = None
    redis_namespace: str = Field(default="evoagent-local", pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    service_concurrency: int = Field(default=4, ge=1, le=64)
    service_requests_per_second: float = Field(default=2.0, gt=0, le=10000)
    service_burst: int = Field(default=4, ge=1, le=10000)
    rate_wait_seconds: float = Field(default=2.0, gt=0, le=60)
    lease_seconds: float = Field(default=30.0, gt=0, le=3_600)
    heartbeat_seconds: float = Field(default=10.0, gt=0, le=1_200)
    snapshot_schema_version: int = Field(default=2, ge=1, le=2)
    artifact_root: Path = Path("./workspace/artifacts")
    artifact_scan_inline_max_bytes: int = Field(default=8 * 1024 * 1024, ge=1, le=64 * 1024 * 1024)
    artifact_scan_cpu_ms: int = Field(default=250, ge=1, le=10_000)
    artifact_scan_wall_ms: int = Field(default=1000, ge=1, le=30_000)
    artifact_scan_concurrency: int = Field(default=2, ge=1, le=8)
    artifact_scan_queue_size: int = Field(default=16, ge=0, le=128)
    max_retry_attempts: int = Field(default=3, ge=1, le=100)
    retry_base_seconds: float = Field(default=1.0, gt=0, le=3_600)
    retry_max_seconds: float = Field(default=30.0, gt=0, le=86_400)
    retry_max_elapsed_seconds: float = Field(default=300.0, gt=0, le=86_400)
    sse_poll_seconds: float = Field(default=0.5, gt=0, le=30)
    sse_heartbeat_seconds: float = Field(default=15.0, gt=0, le=300)
    # 旧宿主 allowlist 仅保留受信 fixture；Worker 需显式选择容器 Profile。
    shell_allowed_executables: tuple[str, ...] = ()
    sandbox_profiles: dict[str, SandboxSpec] = Field(default_factory=dict)
    shell_sandbox_profile: str | None = None
    sandbox_controller_url: str = "http://sandbox-controller:8090"
    sandbox_controller_token: SecretStr | None = None
    sandbox_staging_root: Path = Path("/var/lib/evoagent-sandbox")
    # M3 项目命令契约：结构化 argv、固定 cwd、允许的可执行文件名白名单。
    # 默认为空 = 项目里不能运行任何命令；需要显式列出（例如 python、pytest、node、npm）。
    project_command_allowlist: tuple[str, ...] = ()
    project_command_timeout_seconds: float = Field(default=120.0, gt=0, le=1_800)
    project_command_output_bytes: int = Field(default=65_536, ge=1_024, le=1_048_576)
    project_command_memory_bytes: int = Field(
        default=1_073_741_824, ge=134_217_728, le=4_294_967_296
    )
    # 一条命令的进程树规模上限（含它自己）：内核 RLIMIT_NPROC 与父进程的会话计数两层都用它。
    project_command_max_processes: int = Field(default=256, ge=8, le=4_096)
    # 命令契约允许显式设置的环境变量（例如 PYTHONPATH=src）；
    # 这是"部署者预先声明"的集合，模型不能自行指定环境变量。
    project_command_environment: dict[str, str] = Field(default_factory=dict)
    # 默认离线：一次"允许联网"的批准不会变成长期网络权限。
    project_command_network_default: bool = False
    # Explicitly opt into native Windows execution. This is a trusted local mode:
    # Windows commands are not protected by the Linux Landlock/seccomp sandbox.
    trusted_host_mode: bool = False
    # 产物下载上限：超过即拒绝导出，不把"下载"变成绕过上下文预算读任意文件的通道。
    artifact_download_max_bytes: int = Field(default=16_777_216, ge=1_024, le=268_435_456)

    # M0c 数值预算闸门。金额为"微元"整数，避免浮点误差；**默认全为 None 表示未填数值**，
    # 未填时任何付费模型调用都会被拒绝 —— 计划要求"未填数值不得启动正式评测"。
    # 试跑上限必须在首次付费试跑前填写；正式额度在试跑实测单任务费用后填写。
    budget_trial_limit_micros: int | None = Field(default=None, ge=0)
    budget_total_limit_micros: int | None = Field(default=None, ge=0)
    budget_milestone_limits_micros: dict[str, int] = Field(default_factory=dict)
    # 每 Task 限额：0 表示每个 Task 都不允许付费调用（等价于关闭付费路径）。
    budget_task_limit_micros: int | None = Field(default=None, ge=0)
    # 价格假设：每百万 token 的微元单价；未填时无法计算费用，付费调用同样被拒绝。
    budget_input_price_micros_per_million: int | None = Field(default=None, ge=0)
    budget_output_price_micros_per_million: int | None = Field(default=None, ge=0)
    # 停止阈值：已花费达到上限的这个比例时停止开始新调用（默认 100% = 触及上限才停）。
    budget_stop_ratio: float = Field(default=1.0, gt=0, le=1.0)
    # 付费分账：`trial` 走试跑额度，其余走正式额度；试跑不会挤占正式额度。
    budget_scope: Literal["trial", "formal"] = "formal"

    search_provider: Literal["mock", "brave", "ddgs"] = "mock"
    search_api_key: SecretStr | None = None

    # Skill 仍受项目授权、风险档位和工具审批约束；这里允许提炼项目任务所需的工具。
    code_version: str = Field(default="0.4.0.dev0", min_length=1, max_length=128)
    skill_schema_version: int = Field(default=1, ge=1)
    skill_max_steps: int = Field(default=20, ge=1, le=100)
    skill_max_sources: int = Field(default=10, ge=1, le=100)
    skill_min_sources: int = Field(default=2, ge=1, le=100)
    skill_retrieval_top_k: int = Field(default=1, ge=0, le=3)
    skill_retrieval_min_score: float = Field(default=0.1, ge=0)
    skill_max_effective_risk: ToolRisk = ToolRisk.R1
    skill_allowed_tools: tuple[str, ...] = (
        "calculator",
        "file_read",
        "list_dir",
        "find_files",
        "search_text",
        "extract_text",
        "edit_file",
        "apply_patch",
        "run_command",
        "web_fetch",
        "web_search",
        "artifact_write",
    )
    eval_dataset_root: Path = Path("./evals/datasets")
    skill_extractor_model: str | None = Field(default=None, max_length=256)
    memory_extractor_model: str | None = Field(default=None, max_length=256)
    retrieval_backend: Literal["lexical", "hybrid"] = "lexical"
    memory_retrieval_enabled: bool = False
    archive_retrieval_enabled: bool = False
    embedding_model: str = "mock-hash-v1"
    embedding_dimension: int = Field(default=1536, ge=1, le=2000)
    embedding_preprocessing: Literal["text-v1", "fastembed-0.7.4-mean-v1"] = "text-v1"
    embedding_api_key: SecretStr | None = None
    embedding_base_url: AnyHttpUrl | None = None
    memory_retrieval_top_k: int = Field(default=3, ge=0, le=10)
    retrieval_min_lexical_score: float = Field(default=0.1, gt=0)
    retrieval_max_vector_distance: float = Field(default=0.35, ge=0, lt=1)
    retrieval_rrf_k: int = Field(default=60, ge=1)
    retrieval_skill_budget: int = Field(default=4000, ge=0, le=32000)
    retrieval_memory_budget: int = Field(default=2000, ge=0, le=32000)
    eval_repeats: int = Field(default=3, ge=1, le=100)
    eval_poll_seconds: float = Field(default=1.0, gt=0, le=60)
    eval_lease_seconds: float = Field(default=60.0, gt=0, le=3_600)
    frontend_dist: Path = Path("./frontend/dist")
    mcp_launch_profiles: dict[str, LaunchProfile] = Field(default_factory=dict)
    mcp_http_profiles: dict[str, HTTPProfile] = Field(default_factory=dict)
    # alias -> 环境变量名，不包含密钥值。
    mcp_secret_refs: dict[str, str] = Field(default_factory=dict)
    mcp_max_connections: int = Field(default=8, ge=1, le=32)

    @field_validator("database_url", mode="before")
    @classmethod
    def validate_database_url(cls, value: object) -> str:
        """只接受阶段二支持的异步数据库连接格式。"""

        raw_value = value.get_secret_value() if isinstance(value, SecretStr) else str(value)
        normalized = raw_value.strip()
        allowed = ("postgresql+asyncpg://", "sqlite+aiosqlite://")
        if not normalized.startswith(allowed):
            raise ValueError("database_url must use postgresql+asyncpg or sqlite+aiosqlite")
        return normalized

    @field_validator(
        "skill_extractor_model",
        "memory_extractor_model",
        "embedding_base_url",
        "embedding_api_key",
        "base_url",
        mode="before",
    )
    @classmethod
    def normalize_optional_connection_and_model(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @model_validator(mode="after")
    def validate_provider_requirements(self) -> Self:
        """仅在使用真实模型服务时要求提供连接信息。"""

        if self.trusted_host_mode:
            if os.name != "nt":
                raise ValueError("trusted host mode requires a Windows process")
            if self.api_host not in {"127.0.0.1", "localhost", "::1"}:
                raise ValueError("trusted host API must bind to loopback")

        if (
            self.retrieval_backend == "hybrid"
            or self.memory_retrieval_enabled
            or self.archive_retrieval_enabled
        ) and self.snapshot_schema_version != 2:
            raise ValueError("context retrieval requires snapshot_version 2")

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

        if self.context_policy == "bounded":
            if self.context_window_tokens <= self.max_output_tokens + self.context_safety_margin:
                raise ValueError("context window must exceed output reserve and safety margin")
            if self.provider is ProviderName.OPENAI_COMPATIBLE and (
                "context_window_tokens" not in self.model_fields_set
            ):
                raise ValueError("bounded real provider requires EVOAGENT_CONTEXT_WINDOW_TOKENS")
            # K1：strict 只接受被证实过的计数（`context_policy.prepare` 要求
            # exact/exact_mock/verified_upper_bound）。`policy_from_settings` 只为 Mock 提供
            # MockCounter("exact_mock")；真实 Provider 拿到的是
            # ConservativeTokenCounter("estimated")，于是**每一次**请求都会以
            # context_count_unverified 被拒，Agent 完全不可用。这里在启动时拒绝该组合。
            # 保留运行时的 strict 闸门不放松；将来有了 verified counter 再改成能力校验，
            # 不能为了可用性把估算标成 verified。legacy 模式不走该判断，不受影响。
            if self.context_strict and self.provider is not ProviderName.MOCK:
                raise ValueError(
                    "context_strict requires a verified token counter, but "
                    f"{self.provider.value} can only provide an estimated count; every request "
                    "would be rejected with context_count_unverified"
                )
        self.workspace = self.workspace.expanduser().resolve(strict=False)
        if "artifact_root" not in self.model_fields_set:
            self.artifact_root = self.workspace / "artifacts"
        else:
            self.artifact_root = self.artifact_root.expanduser().resolve(strict=False)
        if self.artifact_root == self.workspace or not self.artifact_root.is_relative_to(
            self.workspace
        ):
            raise ValueError("artifact_root must be a child directory of workspace")
        if self.heartbeat_seconds * 3 > self.lease_seconds:
            raise ValueError("heartbeat_seconds must not exceed one third of lease_seconds")
        if self.retry_base_seconds > self.retry_max_seconds:
            raise ValueError("retry_base_seconds must not exceed retry_max_seconds")
        if self.search_provider == "brave" and (
            self.search_api_key is None or not self.search_api_key.get_secret_value().strip()
        ):
            raise ValueError("brave search provider requires EVOAGENT_SEARCH_API_KEY")
        self.eval_dataset_root = self.eval_dataset_root.expanduser().resolve(strict=False)
        self.frontend_dist = self.frontend_dist.expanduser().resolve(strict=False)
        if len(set(self.skill_allowed_tools)) != len(self.skill_allowed_tools):
            raise ValueError("skill_allowed_tools cannot contain duplicates")
        if "shell" in self.skill_allowed_tools:
            raise ValueError("shell cannot be enabled for declarative skills")
        if self.skill_min_sources > self.skill_max_sources:
            raise ValueError("skill_min_sources cannot exceed skill_max_sources")
        if self.skill_max_effective_risk not in (ToolRisk.R0, ToolRisk.R1):
            raise ValueError("declarative skills cannot exceed R1 in phase three")
        return self
