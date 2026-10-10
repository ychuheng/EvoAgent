import json
from pathlib import Path
from uuid import UUID

import pytest
from pydantic import ValidationError
from test_run_config_and_manifest import snapshot

from evoagent.runtime.run_config import RunConfigSnapshot, RunMode, sha256_text
from evoagent.skills.selection_snapshot import (
    SkillSelectionScope,
    SkillSelectionSnapshot,
    selection_hash,
)

IDENTITY = UUID("00000000-0000-0000-0000-000000000002")


def v3(*, selection=None, target=None, mode=RunMode.RETRIEVAL, **changes):
    selections = () if selection is None else (selection,)
    data = dict(
        schema_version=3,
        run_mode=mode,
        selector_version="skill-selector-v1",
        renderer_version=2,
        skill_renderer_version=2,
        selected_skills=selections,
        under_test_skill_version_id=target,
        skill_selection_hash=selection_hash(
            selector_version="skill-selector-v1", renderer_version=2, selections=selections
        ),
        skill_version_id=selection.version_id if selection else None,
        skill_content_hash=selection.content_hash if selection else None,
        skill_context_hash=selection.rendered_hash if selection else None,
    )
    data.update(changes)
    return snapshot(**data)


def binding(**changes):
    data = dict(
        version_id=IDENTITY,
        content_hash=sha256_text("method"),
        rendered_hash=sha256_text("view"),
        origin="formal",
        scope=SkillSelectionScope(workspace_id=IDENTITY),
    )
    data.update(changes)
    return SkillSelectionSnapshot(**data)


@pytest.mark.parametrize("version", [1, 2])
def test_legacy_canonical_bytes_match_captured_pre_v3_oracle(version):
    # Captured before this refactor from product commit 52b2c176, not derived
    # from today's serializer or merely compared with another new instance.
    oracle = json.loads((Path(__file__).parents[1] / "fixtures/run_config/pre_v3.json").read_text())
    config = snapshot(schema_version=version)
    assert config.canonical_dict() == oracle[str(version)]["body"]
    assert config.content_hash() == oracle[str(version)]["hash"]
    assert (
        RunConfigSnapshot.model_validate(oracle[str(version)]["body"]).content_hash()
        == config.content_hash()
    )


def test_v3_pinned_target_is_not_an_actual_injection_claim():
    unapplied = v3(mode=RunMode.PINNED_SKILL, target=IDENTITY)
    assert unapplied.skill_version_id is None and unapplied.selected_skills == ()
    assert unapplied.comparable_with(v3(mode=RunMode.BASELINE))
    assert unapplied.content_hash() != v3(mode=RunMode.BASELINE).content_hash()
    restored = RunConfigSnapshot.model_validate_json(unapplied.model_dump_json())
    assert restored == unapplied and restored.content_hash() == unapplied.content_hash()
    applied = v3(selection=binding(origin="pinned"), target=IDENTITY, mode=RunMode.PINNED_SKILL)
    assert applied.content_hash() != unapplied.content_hash()
    assert applied.comparable_with(unapplied)


def test_v3_freezes_rendering_and_scope_as_well_as_definition_hash():
    original = v3(selection=binding())
    other_view = v3(selection=binding(rendered_hash=sha256_text("another view")))
    other_scope = v3(
        selection=binding(scope=SkillSelectionScope(workspace_id=IDENTITY, project_id=IDENTITY))
    )
    assert (
        len({original.content_hash(), other_view.content_hash(), other_scope.content_hash()}) == 3
    )
    assert (
        len(
            {
                original.skill_selection_hash,
                other_view.skill_selection_hash,
                other_scope.skill_selection_hash,
            }
        )
        == 3
    )
    assert isinstance(original.selected_skills, tuple)
    with pytest.raises(ValidationError):
        original.selected_skills[0].rendered_hash = sha256_text("tamper")


@pytest.mark.parametrize(
    "changes",
    [
        {"skill_selection_hash": sha256_text("tamper")},
        {"skill_context_hash": sha256_text("stale view")},
        {"skill_content_hash": sha256_text("stale method")},
        {"skill_retrieval_top_k": 2},
        {"skill_renderer_version": 1},
        {"selector_version": None},
        {"selected_skills": None},
        {"under_test_skill_version_id": IDENTITY},
    ],
)
def test_v3_rejects_incomplete_or_inconsistent_metadata(changes):
    with pytest.raises(ValidationError):
        v3(selection=binding(), **changes)


def test_experiment_targets_and_trial_identities_cannot_cross_origin():
    with pytest.raises(ValidationError):
        v3(mode=RunMode.PINNED_SKILL)
    with pytest.raises(ValidationError):
        v3(selection=binding(origin="pinned"))
    with pytest.raises(ValidationError):
        binding(trial_id=IDENTITY)
    with pytest.raises(ValidationError):
        binding(origin="trial")
    with pytest.raises(ValidationError):
        snapshot(selector_version="skill-selector-v1")
    with pytest.raises(ValidationError):
        snapshot(schema_version=2, selected_skills=(binding(),))
