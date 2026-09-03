from pathlib import Path

import pytest
from pydantic import ValidationError

from evoagent.config import LogLevel, ProviderName, Settings


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
