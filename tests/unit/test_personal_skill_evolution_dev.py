import json
from uuid import uuid4

import pytest

from scripts.personal_skill_evolution_dev import emit_evidence, read_fixture


@pytest.mark.parametrize(
    "changes",
    [
        {"data_role": "holdout"},
        {"schema_version": True},
        {"scenarios": ["reuse-revision-brake", "reuse-revision-brake"]},
        {"scenarios": ["--some-pytest-option"]},
        {"scenarios": []},
        {"provider": "real"},
    ],
)
def test_explicit_dev_manifest_rejects_role_options_duplicates_and_paid_configuration(
    tmp_path, changes
):
    path = tmp_path / "fixture.json"
    path.write_text(
        json.dumps(
            {"schema_version": 1, "data_role": "dev", "scenarios": ["reuse-revision-brake"]}
            | changes
        )
    )
    with pytest.raises(ValueError, match="dev fixture"):
        read_fixture(path)


def test_dev_export_is_opt_in_and_serializes_actual_ids(tmp_path, monkeypatch):
    monkeypatch.delenv("EVOAGENT_DEV_EVIDENCE_DIRECTORY", raising=False)
    identity = uuid4()
    emit_evidence("reuse-revision-brake", {"run_id": identity})
    assert not list(tmp_path.iterdir())
    monkeypatch.setenv("EVOAGENT_DEV_EVIDENCE_DIRECTORY", str(tmp_path))
    emit_evidence("reuse-revision-brake", {"run_id": identity})
    assert json.loads((tmp_path / "reuse-revision-brake.json").read_text())["run_id"] == str(
        identity
    )
