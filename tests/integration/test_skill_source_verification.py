"""Physical learning evidence checks use owned files and isolated databases."""

from pathlib import Path

import pytest
from sqlalchemy import select

from evoagent.db.models import LearningSourceRecord
from evoagent.memory.schema import MemoryError
from evoagent.skills.selection_snapshot import SkillSelectionScope
from evoagent.skills.source_verification import SkillSourceVerifier
from evoagent.trace.artifacts import LocalArtifactStore

pytest_plugins = ("tests.integration.test_personal_trials",)


def verifier(context, settings, **kwargs):
    _, db, _, _, _, skill = context
    return SkillSourceVerifier(
        db.session_factory,
        settings,
        SkillSelectionScope(workspace_id=skill.workspace_id),
        **kwargs,
    )


async def test_physical_evidence_tamper_is_not_hidden_by_valid_metadata(
    trial_candidate, learning_api
):
    _, _, _, settings = learning_api
    instance = verifier(trial_candidate, settings)
    _, _, _, _, version, _ = trial_candidate
    assert await instance.verify(version.id) == version.content_hash
    _, proofs = await instance._proofs(version.id)
    assert len(proofs) == 1
    proof = next(iter(proofs.values()))
    root = Path(settings.artifact_root).resolve()
    target = (root / proof.uri).resolve()
    assert target.is_relative_to(root)
    target.write_bytes(b"modified isolated evidence")
    with pytest.raises(MemoryError, match="verification_failed"):
        await instance.verify(version.id)


async def test_source_revocation_during_physical_read_is_refused(trial_candidate, learning_api):
    _, db, _, _, version, _ = trial_candidate
    _, _, _, settings = learning_api

    class RevokingStore(LocalArtifactStore):
        async def read_bounded(self, uri, *, max_bytes):
            raw = await super().read_bounded(uri, max_bytes=max_bytes)
            async with db.session_factory() as session:
                source = await session.scalar(select(LearningSourceRecord))
                source.status = "revoked"
                await session.commit()
            return raw

    instance = verifier(trial_candidate, settings, store=RevokingStore(settings.artifact_root))
    with pytest.raises(MemoryError, match="verification_failed"):
        await instance.verify(version.id)


async def test_source_scan_count_and_total_deadline_fail_closed(trial_candidate, learning_api):
    import asyncio

    _, _, _, settings = learning_api
    _, _, _, _, version, _ = trial_candidate
    instance = verifier(trial_candidate, settings)
    instance.MAX_ARTIFACTS = 0
    with pytest.raises(MemoryError, match="scan_budget_exceeded"):
        await instance.verify(version.id)

    class SlowStore(LocalArtifactStore):
        async def read_bounded(self, uri, *, max_bytes):
            await asyncio.sleep(1)
            return await super().read_bounded(uri, max_bytes=max_bytes)

    instance = verifier(trial_candidate, settings, store=SlowStore(settings.artifact_root))
    instance.TOTAL_SCAN_SECONDS = 0.01
    with pytest.raises(MemoryError, match="verification_failed"):
        await instance.verify(version.id)
