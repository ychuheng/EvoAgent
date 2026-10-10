import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from evoagent.evals.coordinator import EvalExperimentConfig
from evoagent.skills.canonical import content_hash


def test_legacy_experiment_config_matches_pre_v3_frozen_bytes():
    oracle = json.loads(
        (Path(__file__).parents[1] / "fixtures/run_config/eval_pre_v3.json").read_text()
    )
    restored = EvalExperimentConfig.model_validate(oracle["body"])
    assert restored.model_dump(mode="json") == oracle["body"]
    assert content_hash(restored.model_dump(mode="json")) == oracle["hash"]


def test_v3_selection_profile_is_part_of_the_experiment_identity():
    old = EvalExperimentConfig(provider="mock", model="mock", repeats=1, code_version="test")
    new = old.model_copy(update={"selection_contract_version": 3})
    assert new.model_dump(mode="json")["selection_contract_version"] == 3
    assert content_hash(old.model_dump(mode="json")) != content_hash(new.model_dump(mode="json"))


def test_unknown_selection_profile_is_refused():
    with pytest.raises(ValidationError):
        EvalExperimentConfig(
            provider="mock",
            model="mock",
            repeats=1,
            code_version="test",
            selection_contract_version=999,
        )
