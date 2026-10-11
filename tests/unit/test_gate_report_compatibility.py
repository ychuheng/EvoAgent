from uuid import uuid4

import pytest
from pydantic import ValidationError

from evoagent.evals.gates import GateReport
from evoagent.skills.canonical import content_hash


def test_legacy_report_serialization_and_hash_are_byte_compatible():
    # A persisted v1 body must survive round-trip without adding a null field.
    body = {
        "schema_version": 1,
        "skill_version_id": str(uuid4()),
        "experiment_id": str(uuid4()),
        "passed": False,
        "checks": [
            {
                "name": "old",
                "layer": "source",
                "passed": False,
                "threshold": None,
                "actual": None,
                "evidence": {},
            }
        ],
    }
    report = GateReport.model_validate(body)
    assert report.model_dump(mode="json") == body
    assert report.report_hash() == content_hash(body)
    assert GateReport.model_validate_json(report.model_dump_json()).report_hash() == content_hash(
        body
    )


def test_new_source_policy_is_explicit_and_cannot_change_a_legacy_hash():
    body = {
        "skill_version_id": str(uuid4()),
        "experiment_id": str(uuid4()),
        "passed": False,
        "checks": [],
    }
    for patch in (
        {"schema_version": 1, "source_policy_version": "formal-source:v1"},
        {"schema_version": 2},
        {"schema_version": 2, "source_policy_version": "future-policy"},
    ):
        with pytest.raises(ValidationError):
            GateReport.model_validate({**body, **patch})
    report = GateReport.model_validate(
        {**body, "schema_version": 2, "source_policy_version": "formal-source:v1"}
    )
    assert report.model_dump(mode="json")["source_policy_version"] == "formal-source:v1"
    assert report.report_hash() != GateReport.model_validate(body).report_hash()


def test_renaming_same_input_cannot_create_an_independent_formal_source():
    from types import SimpleNamespace

    from evoagent.skills.provenance import formal_input_fingerprint

    digest = "sha256:" + "1" * 64
    first = SimpleNamespace(
        goal="first instruction", input_refs=[{"path": "first.csv", "content_hash": digest}]
    )
    copied = SimpleNamespace(
        goal="paraphrased instruction", input_refs=[{"path": "other.csv", "content_hash": digest}]
    )
    assert formal_input_fingerprint(first) == formal_input_fingerprint(copied)
    unknown = SimpleNamespace(goal="unknown input", input_refs=[{"path": "file.csv"}])
    assert formal_input_fingerprint(unknown) is None


def test_real_source_dto_ignores_prompt_changes_when_declared_data_is_identical():
    from types import SimpleNamespace

    from evoagent.skills.provenance import formal_input_fingerprint

    data = {"origin": "task_input_manifest", "hash": "sha256:" + "a" * 64, "kind": "file"}
    first = SimpleNamespace(
        goal="first", input_refs=[{"origin": "task_goal", "hash": "sha256:" + "1" * 64}, data]
    )
    second = SimpleNamespace(
        goal="second", input_refs=[{"origin": "task_goal", "hash": "sha256:" + "2" * 64}, data]
    )
    assert formal_input_fingerprint(first) == formal_input_fingerprint(second)
