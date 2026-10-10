"""Exact vector scores use the existing index and synthetic hash embeddings."""

import pytest

from evoagent.db.models import (
    DocumentEmbeddingRecord,
    EmbeddingProfileRecord,
    RetrievalDocumentRecord,
    TaskRecord,
)
from evoagent.retrieval.embeddings import EmbeddingProfile, MockEmbeddingProvider
from evoagent.sessions.service import text_hash
from evoagent.skills.applicability import VerifiedSkillFacts
from evoagent.skills.selection_runtime import resolve_ordinary
from evoagent.skills.selection_vectors import vector_scores
from evoagent.tasks.lease_guard import LeaseLostError
from tests.integration.test_skill_selection_runtime import setup_run
from tests.integration.test_skill_selector_candidates import formal_fixture

pytest_plugins = ("tests.integration.test_personal_trials",)


async def seeded_index(db, settings, candidate):
    identity = EmbeddingProfile(settings.embedding_model, settings.embedding_dimension)
    result = await MockEmbeddingProvider().embed((candidate.document.text,), identity)
    async with db.session_factory() as session:
        profile = EmbeddingProfileRecord(
            model=identity.model, dimension=identity.dimension, active_generation=1
        )
        document = RetrievalDocumentRecord(
            source_key=f"skill:{candidate.document.version_id}",
            skill_version_id=candidate.document.version_id,
            workspace_id=candidate.scope.workspace_id,
            input_hash=text_hash(candidate.document.text[:12000]),
            source_hash=candidate.document.content_hash,
        )
        session.add_all((profile, document))
        await session.flush()
        session.add(
            DocumentEmbeddingRecord(
                document_id=document.id,
                profile_id=profile.id,
                generation=1,
                input_hash=document.input_hash,
                vector=list(result.vectors[0]),
            )
        )
        await session.commit()
    return profile.id, document.id


async def test_filtered_vectors_are_frozen_and_restore_needs_no_embedding(
    trial_candidate, learning_api, monkeypatch
):
    await formal_fixture(trial_candidate)
    _, db, _, original = learning_api
    settings = original.model_copy(update={"retrieval_backend": "hybrid"})
    selector, task, run = await setup_run(trial_candidate, settings)
    candidates = await selector.candidates()
    profile_id, _ = await seeded_index(db, settings, candidates[0])
    facts = VerifiedSkillFacts.from_runtime(selector.registry, task_family="general")
    scores = await vector_scores(selector, task.goal, candidates, facts=facts)
    assert scores.profile_id == profile_id and scores.generation == 1
    assert scores.degraded is None and set(scores.distances) == {candidates[0].document.version_id}
    choice = await resolve_ordinary(selector, task, run)
    assert len(choice.selections) == 1

    def no_provider(*_):
        raise AssertionError("restoring a choice must not embed again")

    monkeypatch.setattr("evoagent.skills.selection_vectors.provider_from_settings", no_provider)
    assert await resolve_ordinary(selector, task, run) == choice


async def test_unknown_facts_or_stale_index_do_not_call_embedding(trial_candidate, learning_api):
    await formal_fixture(trial_candidate)
    _, db, _, settings = learning_api
    selector, task, _ = await setup_run(trial_candidate, settings)
    candidates = await selector.candidates()
    _, document_id = await seeded_index(db, settings, candidates[0])

    class Never:
        async def embed(self, *_):
            raise AssertionError("ineligible or stale input must not request vectors")

    unknown = VerifiedSkillFacts.from_runtime(selector.registry)
    assert (
        await vector_scores(selector, task.goal, candidates, facts=unknown, provider=Never())
    ).distances == {}
    async with db.session_factory() as session:
        row = await session.get(RetrievalDocumentRecord, document_id)
        row.input_hash = "sha256:" + "a" * 64
        await session.commit()
    facts = VerifiedSkillFacts.from_runtime(selector.registry, task_family="general")
    scores = await vector_scores(selector, task.goal, candidates, facts=facts, provider=Never())
    assert scores.degraded == "index_not_ready" and scores.distances == {}


async def test_fenced_embedding_cannot_return_scores_to_the_old_owner(
    trial_candidate, learning_api
):
    await formal_fixture(trial_candidate)
    _, db, _, settings = learning_api
    selector, task, _ = await setup_run(trial_candidate, settings)
    candidates = await selector.candidates()
    await seeded_index(db, settings, candidates[0])

    class Fence(MockEmbeddingProvider):
        async def embed(self, texts, profile):
            result = await super().embed(texts, profile)
            async with db.session_factory() as session:
                row = await session.get(TaskRecord, task.id)
                row.lease_epoch += 1
                await session.commit()
            return result

    facts = VerifiedSkillFacts.from_runtime(selector.registry, task_family="general")
    with pytest.raises(LeaseLostError):
        await vector_scores(selector, task.goal, candidates, facts=facts, provider=Fence())
