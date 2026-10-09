"""Fixed hashes produced by the unmodified 72cc330 definitions, not this implementation."""

import json
from pathlib import Path

import pytest

from evoagent.evals.gates import QualityGate
from evoagent.evals.metrics import EvaluationReport
from evoagent.evals.schema import EvalDatasetDefinition
from evoagent.skills.canonical import content_hash


def test_formal_dataset_and_report_keep_their_original_hashes():
    fixtures = json.loads(
        (Path(__file__).parents[1] / "fixtures/validation/legacy_hash_cases.json").read_text("utf8")
    )
    dataset = EvalDatasetDefinition.model_validate(fixtures["dataset"])
    report = EvaluationReport.model_validate(fixtures["report"])
    assert content_hash(dataset.model_dump(mode="json")) == (
        "sha256:af8de06a9c1ebbf8ecac32089d4a98da4352d600d270b3fc6adbd76ae474eea6"
    )
    assert report.report_hash() == (
        "sha256:c6348073b79275dcbe7917b3520eee8c8355b9af65a3706928e8a9188c153e9f"
    )


def test_personal_purpose_is_part_of_new_dataset_identity():
    fixtures = json.loads(
        (Path(__file__).parents[1] / "fixtures/validation/legacy_hash_cases.json").read_text("utf8")
    )
    formal = EvalDatasetDefinition.model_validate(fixtures["dataset"])
    personal = formal.model_copy(update={"purpose": "personal_dev"})
    assert content_hash(formal.model_dump(mode="json")) != content_hash(
        personal.model_dump(mode="json")
    )


async def test_personal_report_cannot_pass_the_formal_publication_gate():
    fixtures = json.loads(
        (Path(__file__).parents[1] / "fixtures/validation/legacy_hash_cases.json").read_text("utf8")
    )
    report = EvaluationReport.model_validate(fixtures["report"]).model_copy(
        update={"purpose": "personal_validation", "schema_version": 2}
    )
    with pytest.raises(ValueError, match="personal"):
        await QualityGate(None, None, None, None, minimum_sources=1).evaluate(report)
