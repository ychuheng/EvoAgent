from pathlib import Path

import pytest
from pydantic import ValidationError

from evoagent.config import LogLevel, ProviderName, Settings
from evoagent.core.models import ToolRisk


def test_defaults_use_mock_provider_and_resolve_workspace(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, workspace=tmp_path / "workspace")

    assert settings.provider is ProviderName.MOCK
    assert settings.workspace == (tmp_path / "workspace").resolve()
    assert settings.max_iterations == 8
    assert settings.log_level is LogLevel.INFO
    assert settings.api_key is None


def test_environment_variables_override_defaults(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("EVOAGENT_MAX_ITERATIONS", "12")
    monkeypatch.setenv("EVOAGENT_LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("EVOAGENT_WORKSPACE", str(tmp_path))

    settings = Settings(_env_file=None)

    assert settings.max_iterations == 12
    assert settings.log_level is LogLevel.DEBUG
    assert settings.workspace == tmp_path.resolve()


def test_openai_compatible_provider_requires_connection_settings() -> None:
    with pytest.raises(ValidationError, match="EVOAGENT_API_KEY"):
        Settings(_env_file=None, provider=ProviderName.OPENAI_COMPATIBLE)


def test_openai_compatible_provider_accepts_complete_configuration() -> None:
    settings = Settings(
        _env_file=None,
        provider=ProviderName.OPENAI_COMPATIBLE,
        api_key="test-secret",
        base_url="https://llm.example.test/v1",
        model="example-model",
    )

    assert settings.provider is ProviderName.OPENAI_COMPATIBLE
    assert settings.api_key is not None
    assert settings.api_key.get_secret_value() == "test-secret"
    assert str(settings.base_url) == "https://llm.example.test/v1"
    assert settings.model == "example-model"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("max_iterations", 0),
        ("model_timeout_seconds", 0),
        ("task_timeout_seconds", -1),
        ("tool_timeout_seconds", 0),
        ("max_total_tokens", 0),
        ("max_repeated_tool_calls", 0),
        ("max_tool_result_chars", 0),
    ],
)
def test_runtime_limits_must_be_positive(field: str, value: int) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{field: value})


def test_stage_two_database_and_worker_defaults(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        workspace=tmp_path,
        artifact_root=tmp_path / "artifacts",
    )

    assert settings.database_url.get_secret_value().startswith("postgresql+asyncpg://")
    assert settings.api_host == "127.0.0.1"
    assert settings.api_port == 8_000
    assert settings.heartbeat_seconds * 3 <= settings.lease_seconds
    assert settings.snapshot_schema_version == 1
    assert settings.max_retry_attempts == 3
    assert settings.retry_base_seconds <= settings.retry_max_seconds
    assert settings.artifact_root == (tmp_path / "artifacts").resolve()


def test_database_url_requires_supported_async_driver() -> None:
    with pytest.raises(ValidationError, match=r"postgresql\+asyncpg"):
        Settings(_env_file=None, database_url="postgresql://localhost/evoagent")


def test_heartbeat_must_fit_inside_lease() -> None:
    with pytest.raises(ValidationError, match="one third"):
        Settings(_env_file=None, lease_seconds=30, heartbeat_seconds=11)


def test_retry_base_must_not_exceed_maximum() -> None:
    with pytest.raises(ValidationError, match="retry_base_seconds"):
        Settings(_env_file=None, retry_base_seconds=31, retry_max_seconds=30)


def test_artifact_root_must_be_below_workspace(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="child directory"):
        Settings(
            _env_file=None,
            workspace=tmp_path / "workspace",
            artifact_root=tmp_path / "outside",
        )


def test_stage_three_skill_configuration_relations() -> None:
    with pytest.raises(ValidationError, match="cannot exceed"):
        Settings(_env_file=None, skill_min_sources=3, skill_max_sources=2)
    with pytest.raises(ValidationError, match="cannot exceed R1"):
        Settings(_env_file=None, skill_max_effective_risk=ToolRisk.R2)
    with pytest.raises(ValidationError, match="shell"):
        Settings(_env_file=None, skill_allowed_tools=("calculator", "shell"))
