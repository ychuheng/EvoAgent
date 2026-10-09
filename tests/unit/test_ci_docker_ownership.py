import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("ci_docker", Path("scripts/ci_docker.py"))
ci_docker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ci_docker)


def test_cache_preserves_existing_daemon_settings_and_is_idempotent():
    original = {
        "features": {"containerd-snapshotter": True},
        "registry-mirrors": ["https://existing.invalid"],
    }
    result = ci_docker.cache_configuration(original)
    assert result["features"] == original["features"]
    assert result["registry-mirrors"] == [ci_docker.MIRROR, "https://existing.invalid"]
    assert original["registry-mirrors"] == ["https://existing.invalid"]
    assert ci_docker.cache_configuration(result) == result


@pytest.mark.parametrize("value", [None, [], {"registry-mirrors": "bad"}])
def test_bad_daemon_configuration_is_not_overwritten(value):
    with pytest.raises(ValueError):
        ci_docker.cache_configuration(value)


def test_configure_refuses_local_or_self_hosted_machines_before_any_io(monkeypatch):
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("RUNNER_ENVIRONMENT", "self-hosted")
    monkeypatch.setattr(ci_docker.sys, "platform", "linux")
    with pytest.raises(RuntimeError, match="isolated GitHub-hosted"):
        ci_docker.configure()


def test_cleanup_refuses_a_container_with_other_job_ownership(monkeypatch):
    import json

    monkeypatch.setattr(ci_docker, "runner_owner", lambda: "repo:1:1:test")

    def metadata_only(*args):
        if args[0] == "ps":
            return "candidate-container-id"
        assert args == ("inspect", "candidate-container-id")
        return json.dumps(
            [
                {
                    "Name": "/evoagent-ci-postgres",
                    "Config": {"Labels": {"evoagent.ci.owner": "another-job"}},
                }
            ]
        )

    monkeypatch.setattr(ci_docker, "docker", metadata_only)
    with pytest.raises(RuntimeError, match="outside this CI job"):
        ci_docker.stop()
