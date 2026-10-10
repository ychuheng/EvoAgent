"""Personal-mode checks must fail before deployment without revealing credentials."""

from pathlib import Path

import pytest

from scripts.personal_preflight import validate


def write_config(path: Path, *, extra: str = "") -> None:
    path.write_text(
        "EVOAGENT_API_KEY=private-test-value\n"
        "EVOAGENT_BASE_URL=https://example.com/v1\n"
        "EVOAGENT_MODEL=example-model\n"
        "EVOAGENT_CONTEXT_WINDOW_TOKENS=32768\n"
        f"{extra}",
        encoding="utf-8",
    )


def test_valid_personal_configuration_has_no_remote_side_effect(tmp_path: Path) -> None:
    path = tmp_path / ".env.personal"
    write_config(path, extra="EVOAGENT_SEARCH_PROVIDER=mock\n")

    assert validate(path) == ("example-model", "mock")


def test_keyless_ddgs_personal_configuration(tmp_path: Path) -> None:
    path = tmp_path / ".env.personal"
    write_config(path, extra="EVOAGENT_SEARCH_PROVIDER=ddgs\n")

    assert validate(path) == ("example-model", "ddgs")


@pytest.mark.parametrize(
    "extra, expected",
    [
        ("EVOAGENT_SEARCH_PROVIDER=brave\n", "EVOAGENT_SEARCH_API_KEY"),
        ("EVOAGENT_SEARCH_PROVIDER=brav\n", "search_provider"),
        ("EVOAGENT_SEARCH_PROVIDR=brave\n", "EVOAGENT_SEARCH_PROVIDR"),
        ("EVOAGENT_CONTEXT_WINDOW_TOKENS=12\n", "configuration"),
        ("EVOAGENT_BASE_URL=not-a-url\n", "base_url"),
    ],
)
def test_invalid_configuration_fails_without_secret(
    tmp_path: Path, extra: str, expected: str
) -> None:
    path = tmp_path / ".env.personal"
    write_config(path, extra=extra)

    with pytest.raises(ValueError) as error:
        validate(path)

    assert expected in str(error.value)
    assert "private-test-value" not in str(error.value)


def test_required_fields_are_reported_by_name(tmp_path: Path) -> None:
    path = tmp_path / ".env.personal"
    path.write_text("EVOAGENT_API_KEY=private-test-value\n", encoding="utf-8")

    with pytest.raises(ValueError, match="EVOAGENT_CONTEXT_WINDOW_TOKENS"):
        validate(path)


@pytest.mark.parametrize(
    "extra,expected",
    [
        ("EVOAGENT_PERSONAL_VALIDATION_REAL_ENABLED=true\n", "learning_enabled"),
        (
            "EVOAGENT_LEARNING_ENABLED=true\nEVOAGENT_PERSONAL_VALIDATION_REAL_ENABLED=true\n",
            "validation_budget_unapproved",
        ),
    ],
)
def test_real_validation_preflight_requires_opt_in_and_known_numeric_budget(
    tmp_path, extra, expected
):
    path = tmp_path / ".env.personal"
    write_config(path, extra=extra)
    with pytest.raises(ValueError, match=expected) as error:
        validate(path)
    assert "private-test-value" not in str(error.value)
    assert "example.com" not in str(error.value)


def test_real_validation_profile_check_is_offline(tmp_path, monkeypatch):
    import socket

    def forbidden(*_args, **_kwargs):
        raise AssertionError("preflight must never contact provider")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    path = tmp_path / ".env.personal"
    write_config(
        path,
        extra=(
            "EVOAGENT_LEARNING_ENABLED=true\n"
            "EVOAGENT_PERSONAL_VALIDATION_REAL_ENABLED=true\n"
            "EVOAGENT_BUDGET_SCOPE=trial\n"
            "EVOAGENT_BUDGET_TRIAL_LIMIT_MICROS=100000\n"
            "EVOAGENT_BUDGET_TASK_LIMIT_MICROS=10000\n"
            "EVOAGENT_BUDGET_INPUT_PRICE_MICROS_PER_MILLION=1\n"
            "EVOAGENT_BUDGET_OUTPUT_PRICE_MICROS_PER_MILLION=1\n"
        ),
    )
    assert validate(path) == ("example-model", "mock")
