import pytest
from pydantic import ValidationError

from evoagent.config import Settings
from evoagent.learning.sources import PersonalSourceService
from evoagent.privacy.artifact_access import ArtifactInjectionGuard


@pytest.mark.parametrize(
    "field,value",
    [
        ("runtime_event_batch_count", 33),
        ("runtime_event_batch_bytes", 65537),
        ("runtime_event_flush_seconds", 0.2),
        ("runtime_event_page_size", 201),
        ("runtime_event_fallback_seconds", 3),
        ("recovery_scan_seconds", 3),
        ("recovery_scan_batch_size", 51),
        ("maintenance_idle_max_seconds", 9),
        ("cleanup_poll_max_seconds", 2),
    ],
)
def test_tuning_cannot_relax_verified_runtime_bounds(field, value):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{field: value})


def test_learning_sources_inherit_host_scan_settings_without_default_settings():
    from types import SimpleNamespace

    host = Settings(_env_file=None, runtime_scan_cache_enabled=True, artifact_scan_wall_ms=5000)
    factory = SimpleNamespace(kw={"info": {"runtime_settings": host}})
    source = PersonalSourceService(factory)
    guard = ArtifactInjectionGuard(session_factory=factory, settings=source.settings)
    assert guard._scan_cache_enabled and guard._limits.wall_ms == 5000
    override = host.model_copy(update={"runtime_scan_cache_enabled": False})
    assert PersonalSourceService(factory, settings=override).settings is override
