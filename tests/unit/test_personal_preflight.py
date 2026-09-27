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
