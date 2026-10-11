"""Validate the private personal-mode configuration without contacting paid services."""

import argparse
import json
from pathlib import Path

from dotenv import dotenv_values
from pydantic import ValidationError

from evoagent.config import Settings
from evoagent.projects.readiness import (
    REASON_HARD_PIDS,
    REASON_UNOBSERVABLE,
    ProjectCommandReadinessProbe,
)

REQUIRED = (
    "EVOAGENT_API_KEY",
    "EVOAGENT_BASE_URL",
    "EVOAGENT_MODEL",
    "EVOAGENT_CONTEXT_WINDOW_TOKENS",
)


def validate(path: Path) -> tuple[str, str]:
    """配置校验的公开入口；返回 `(model, search_provider)`，行为保持不变。"""

    settings = _settings_from_env_file(path)
    return settings.model or "", settings.search_provider


def _settings_from_env_file(path: Path) -> Settings:
    """解析并校验配置文件；`validate()` 与宿主就绪预检共用这一条路径。"""

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
    if settings.personal_validation_real_enabled:
        if not settings.learning_enabled:
            raise ValueError("personal validation requires learning_enabled")
        from evoagent.learning.schema import LearningError
        from evoagent.learning.validation_profiles import real_profile

        try:
            real_profile(settings)
        except LearningError as error:
            raise ValueError("personal validation configuration: " + error.code) from None
    return settings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=Path(".env.personal"))
    args = parser.parse_args()
    try:
        _model, _search = validate(args.env_file)
        settings = _settings_from_env_file(args.env_file)
    except ValueError as error:
        parser.exit(2, f"personal configuration error: {error}\n")
    print(f"configuration valid: model={_model}, search={_search}; remote services not checked")
    if settings.learning_enabled:
        print("learning enabled: workspace policy and numeric quotas require explicit approval")
    if settings.personal_validation_real_enabled:
        print("real validation profile registered locally: no network check or paid call performed")
    if settings.personal_trial_enabled:
        print("trial controls enabled: report, source, budget and health gates still apply")
    # K2 / §13.4：这里跑的是**宿主**可观测性检查，它**不能替代**实际 Worker 容器、
    # UID 与命名空间里的预检；两者共用同一个 probe，结论不互为证明。
    result = ProjectCommandReadinessProbe().check(settings)
    print(f"host command readiness: {result.reason} — {result.detail}")
    if result.reason == REASON_HARD_PIDS:
        print(
            "host check failed: delegated per-command cgroup v2 quota unavailable; command disabled"
        )
        return 2
    if result.reason == REASON_UNOBSERVABLE:
        print(
            "host check failed: 该宿主上无法观测会话进程数，项目命令不会在 Worker 中注册；"
            "若在容器里运行，请在实际 Worker 环境重跑本预检"
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
