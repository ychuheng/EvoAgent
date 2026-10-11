import importlib

import pytest
from pydantic import SecretStr

from evoagent.api.app import create_app
from evoagent.config import Settings
from evoagent.learning.bootstrap import assemble_learning
from evoagent.learning.configuration import candidate_configuration
from evoagent.learning.validation_profiles import real_profile
from evoagent.skills.canonical import content_hash


def host(mode):
    return Settings(
        _env_file=None,
        provider="openai_compatible",
        model="fixture-model",
        api_key=SecretStr("fixture-only-credential"),
        base_url="https://never-called.example.test/v1",
        context_window_tokens=32768,
        learning_enabled=True,
        skill_extractor_model="fixture-model",
        memory_extractor_model="fixture-model",
        provider_thinking_mode=mode,
        personal_validation_real_enabled=True,
        budget_total_limit_micros=100000,
        budget_task_limit_micros=10000,
        budget_input_price_micros_per_million=100,
        budget_output_price_micros_per_million=200,
    )


@pytest.mark.parametrize("mode", [None, "disabled"])
def test_all_extractor_provider_assemblies_honor_explicit_host_mode(monkeypatch, mode):
    calls = []

    class Provider:
        def __init__(self, **kwargs):
            calls.append(kwargs["thinking_mode"])

    monkeypatch.setattr("evoagent.providers.openai_compatible.OpenAICompatibleProvider", Provider)
    monkeypatch.setattr(
        importlib.import_module("evoagent.api.app"), "OpenAICompatibleProvider", Provider
    )
    settings = host(mode)
    _, provider = assemble_learning(None, None, settings, None)
    assert isinstance(provider, Provider)
    create_app(settings)
    assert calls == [mode, mode, mode]


def test_none_preserves_legacy_hash_and_disabled_changes_frozen_identities():
    legacy = host(None)
    old_generation = candidate_configuration(legacy)
    old_profile = real_profile(legacy).identity()
    assert "provider_thinking_mode" not in old_generation
    assert "provider_thinking_mode" not in old_profile["profile"]
    assert old_profile["profile_hash"] == content_hash(old_profile["profile"])
    changed = host("disabled")
    assert candidate_configuration(changed)["provider_thinking_mode"] == "disabled"
    assert content_hash(candidate_configuration(changed)) != content_hash(old_generation)
    assert real_profile(changed).identity()["profile_hash"] != old_profile["profile_hash"]


def test_learning_stream_deadline_is_part_of_frozen_generation_identity():
    settings = host(None)
    original = candidate_configuration(settings)
    assert original["model_timeout_seconds"] == settings.model_timeout_seconds
    changed = settings.model_copy(
        update={"model_timeout_seconds": settings.model_timeout_seconds + 1}
    )
    assert content_hash(candidate_configuration(changed)) != content_hash(original)
