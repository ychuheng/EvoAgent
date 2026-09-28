"""Validate the private personal-mode configuration without contacting paid services."""

import argparse
import json
from pathlib import Path

from dotenv import dotenv_values
from pydantic import ValidationError

from evoagent.config import Settings

REQUIRED = (
    "EVOAGENT_API_KEY",
    "EVOAGENT_BASE_URL",
    "EVOAGENT_MODEL",
    "EVOAGENT_CONTEXT_WINDOW_TOKENS",
)


def validate(path: Path) -> tuple[str, str]:
    if not path.is_file():
        raise ValueError(f"configuration file not found: {path}")
    raw = dotenv_values(path)
    missing = [name for name in REQUIRED if not (raw.get(name) or "").strip()]
    if missing:
        raise ValueError("missing required settings: " + ", ".join(missing))
    if (raw.get("EVOAGENT_SEARCH_PROVIDER") or "mock") == "brave" and not (
        raw.get("EVOAGENT_SEARCH_API_KEY") or ""
    ).strip():
        raise ValueError("brave search requires EVOAGENT_SEARCH_API_KEY")

    known = Settings.model_fields
    unknown = sorted(
        name
        for name in raw
        if name.startswith("EVOAGENT_") and name.removeprefix("EVOAGENT_").lower() not in known
    )
    if unknown:
        raise ValueError("unknown settings: " + ", ".join(unknown))
    values = {
        name.removeprefix("EVOAGENT_").lower(): value
        for name, value in raw.items()
        if name.startswith("EVOAGENT_")
        and name.removeprefix("EVOAGENT_").lower() in known
        and value is not None
        and value != ""
    }
    for field in (
        "project_command_allowlist",
        "project_command_environment",
        "budget_milestone_limits_micros",
    ):
        if field in values:
            try:
                values[field] = json.loads(values[field])
            except json.JSONDecodeError:
                raise ValueError(f"invalid settings: {field}") from None
    values["provider"] = "openai_compatible"
    try:
        settings = Settings.model_validate(values)
    except ValidationError as error:
        fields = sorted(
            {
                ".".join(str(part) for part in item["loc"]) or "configuration"
                for item in error.errors()
            }
        )
        raise ValueError("invalid settings: " + ", ".join(fields)) from None
    except ValueError:
        # Model validators have hand-written messages that mention setting names,
        # but never include the secret values supplied in this file.
        raise
    return settings.model or "", settings.search_provider


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=Path(".env.personal"))
    args = parser.parse_args()
    try:
        model, search = validate(args.env_file)
    except ValueError as error:
        parser.exit(2, f"personal configuration error: {error}\n")
    print(f"configuration valid: model={model}, search={search}; remote services not checked")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
