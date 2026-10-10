"""Fenced ordinary v3 selection; historical queued tasks keep their old contract."""

from sqlalchemy import select

from evoagent.db.models import (
    RetrievalBatchRecord,
    RetrievalSelectionRecord,
    RunSkillSelectionRecord,
    SessionRecord,
    SkillTrialRecord,
    utc_now,
)
from evoagent.db.unit_of_work import UnitOfWork
from evoagent.memory.schema import MemoryError
from evoagent.runtime.checkpoints import SnapshotCompatibilityError
from evoagent.sessions.service import text_hash
from evoagent.skills.access import SkillAccessError, SkillAccessPolicy
from evoagent.skills.applicability import VerifiedSkillFacts
from evoagent.skills.canonical import content_hash
from evoagent.skills.selection import FrozenSkillChoice
from evoagent.skills.selection_snapshot import SkillSelectionSnapshot
from evoagent.skills.selection_vectors import SkillVectorScores, vector_scores
from evoagent.skills.service import GateNotPassedError, SkillService
from evoagent.skills.source_verification import SkillSourceVerifier
from evoagent.skills.trials import SkillTrialService, TrialScope


async def _owned(selector, unit, task, run):
    if selector.guard.lease.run_id != run.id or selector.guard.lease.task_id != task.id:
        raise MemoryError("skill_selection_scope_invalid")
    owned_task, owned = await selector.guard.check(unit.session)
    chat = await unit.session.get(SessionRecord, owned_task.session_id)
    if (
        owned.id != run.id
        or owned.task_id != task.id
        or owned.data_role != "personal"
        or owned.run_mode not in {"retrieval", "baseline"}
        or owned.pinned_skill_version_id is not None
        or owned_task.selection_contract_version != 3
        or chat is None
        or chat.workspace_id != selector.scope.workspace_id
        or owned_task.project_id != selector.scope.project_id
        or owned_task.goal != task.goal
        or owned_task.family != task.family
        or owned_task.frozen_inputs != task.frozen_inputs
        or owned.run_mode != run.run_mode
    ):
        raise MemoryError("skill_selection_scope_invalid")
    return owned, chat


async def restore(selector, session, batch):
    from evoagent.skills.retrieval import RetrievalMatch, SkillDocument

    bindings = tuple(
        await session.scalars(
            select(RunSkillSelectionRecord).where(RunSkillSelectionRecord.run_id == batch.run_id)
        )
    )
    rows = tuple(
        await session.scalars(
            select(RetrievalSelectionRecord).where(
                RetrievalSelectionRecord.batch_id == batch.id,
                RetrievalSelectionRecord.omission_reason.is_(None),
            )
        )
    )
    if batch.selected_count == 0:
        if rows or bindings:
            raise MemoryError("skill_selection_corrupt")
        return FrozenSkillChoice((), None, ())
    if batch.selected_count != 1 or len(bindings) != 1 or len(rows) != 1:
        raise MemoryError("skill_selection_corrupt")
    binding, row = bindings[0], rows[0]
    if (
        row.text is None
        or row.source_key != f"skill:{binding.skill_version_id}"
        or row.source_hash != binding.content_hash
        or row.text_hash != text_hash(row.text)
        or row.text_hash != binding.rendered_hash
        or row.rank != 1
        or binding.rank != 1
        or str(binding.mode) != "retrieval"
        or binding.origin not in {"formal", "trial"}
        or binding.selection_policy_version != selector.VERSION
        or binding.scope_key != content_hash(selector.scope.model_dump(mode="json"))
        or binding.applicability != row.evidence.get("applicability")
        or (binding.applicability or {}).get("status") != "applicable"
        or (binding.origin == "trial") != (binding.trial_id is not None)
    ):
        raise MemoryError("skill_selection_corrupt")
    try:
        version = await SkillAccessPolicy().check(
            session,
            binding.skill_version_id,
            workspace_id=selector.scope.workspace_id,
            project_id=selector.scope.project_id,
        )
    except SkillAccessError:
        raise MemoryError("skill_source_revoked") from None
    if version.content_hash != binding.content_hash:
        raise MemoryError("skill_selection_corrupt")
    if binding.origin == "formal":
        if str(version.lifecycle_status) not in {"active", "retired"}:
            raise MemoryError("skill_formal_gate_unavailable")
        try:
            await SkillService.check_formal_gate(session, version)
        except GateNotPassedError:
            raise MemoryError("skill_formal_gate_unavailable") from None
    else:
        trial = await session.get(SkillTrialRecord, binding.trial_id)
        if (
            trial is None
            or trial.status not in {"active", "replaced"}
            or trial.version_id != version.id
            or trial.skill_id != version.skill_id
            or trial.workspace_id != selector.scope.workspace_id
            or trial.project_id not in (None, selector.scope.project_id)
            or trial.scope_key != TrialScope(trial.workspace_id, trial.project_id).key
        ):
            raise MemoryError("skill_trial_unavailable")
        ready = await SkillTrialService(selector.factory)._assess(
            session, version.id, trial.validation_request_id
        )
        if not ready.ready or ready.report_hash != trial.report_hash:
            raise MemoryError("skill_trial_validation_revoked")
    frozen = SkillSelectionSnapshot(
        version_id=version.id,
        content_hash=version.content_hash,
        rendered_hash=row.text_hash,
        origin=binding.origin,
        scope=selector.scope,
        trial_id=binding.trial_id,
    )
    from evoagent.skills.schema import SkillDefinition

    match = RetrievalMatch(
        SkillDocument(
            version.skill_id,
            version.id,
            SkillDefinition.model_validate(version.definition),
            version.content_hash,
        ),
        binding.score,
        tuple(binding.query_terms),
    )
    return FrozenSkillChoice((match,), row.text, (frozen,))


async def _verify_choice(selector, choice, run_id):
    if choice.text is not None:
        await SkillSourceVerifier(selector.factory, selector.settings, selector.scope).verify(
            choice.selections[0].version_id
        )
        await selector._verify_text(choice.text, run_id, choice.selections[0].version_id)


async def resolve_ordinary(selector, task, run):
    facts = VerifiedSkillFacts.from_runtime(
        selector.registry, task.frozen_inputs, task_family=task.family
    )
    config = {
        "selector_version": selector.VERSION,
        "renderer_version": 2,
        "scope": selector.scope.model_dump(mode="json"),
        "mode": run.run_mode,
        "facts_hash": content_hash(facts.as_mapping()),
        "top_k": min(selector.settings.skill_retrieval_top_k, 1),
        "max_risk": selector.settings.skill_max_effective_risk.value,
        "skill_budget": selector.settings.retrieval_skill_budget,
        "backend": "hybrid" if selector.settings.retrieval_backend == "hybrid" else "bm25",
        "minimum_score": selector.settings.skill_retrieval_min_score,
        "rrf_k": selector.settings.retrieval_rrf_k,
        "maximum_distance": selector.settings.retrieval_max_vector_distance,
        "embedding_model": selector.settings.embedding_model,
        "embedding_dimension": selector.settings.embedding_dimension,
        "embedding_preprocessing": selector.settings.embedding_preprocessing,
    }
    async with UnitOfWork(selector.factory) as unit:
        owned, _ = await _owned(selector, unit, task, run)
        saved = await selector._saved(unit.session, run.id)
        if saved is not None:
            selector._check_config(saved, config, task.goal)
            choice = await restore(selector, unit.session, saved)
        else:
            if owned.skill_selection_frozen or owned.config_snapshot is not None:
                raise SnapshotCompatibilityError("legacy selection cannot be upgraded in place")
            choice = None
        await unit.commit()
    if choice is not None:
        await _verify_choice(selector, choice, run.id)
        # Scanner refusal/events happen outside fencing locks; recheck authority afterwards.
        async with UnitOfWork(selector.factory) as unit:
            await _owned(selector, unit, task, run)
            choice = await restore(selector, unit.session, saved)
            await unit.commit()
        return choice
    candidates = await selector.candidates(run_mode=run.run_mode)
    vectors = SkillVectorScores({})
    if config["backend"] == "hybrid":
        vectors = await vector_scores(
            selector,
            task.goal,
            candidates,
            facts=facts,
            service_gate=getattr(selector, "service_gate", None),
        )
    ranked = await selector.rank(
        task.goal, candidates, facts=facts, backend=config["backend"], distances=vectors.distances
    )
    plan = selector.choose(ranked, token_budget=config["skill_budget"], max_skills=config["top_k"])
    if plan.selected is not None:
        version_id = plan.selected.candidate.document.version_id
        await SkillSourceVerifier(selector.factory, selector.settings, selector.scope).verify(
            version_id
        )
        await selector._verify_text(plan.text, run.id, version_id)
    async with UnitOfWork(selector.factory) as unit:
        owned, chat = await _owned(selector, unit, task, run)
        saved = await selector._saved(unit.session, run.id)
        if saved is not None:
            selector._check_config(saved, config, task.goal)
            choice = await restore(selector, unit.session, saved)
        else:
            if owned.skill_selection_frozen or owned.config_snapshot is not None:
                raise SnapshotCompatibilityError("selection changed during preparation")
            await selector.revalidate(unit.session, plan)
            # Graph/readiness checks can take time: expiration must precede all writes.
            await selector.guard.check(unit.session)
            batch = RetrievalBatchRecord(
                run_id=run.id,
                purpose="skill_selector",
                query_hash=text_hash(task.goal),
                workspace_id=chat.workspace_id,
                session_id=chat.id,
                config=config,
                selected_count=int(plan.selected is not None),
                profile_id=vectors.profile_id,
                generation=vectors.generation,
                degraded=vectors.degraded,
            )
            unit.session.add(batch)
            await unit.session.flush()
            for identity, reason in plan.omissions:
                item = next(row for row in ranked if row.candidate.document.version_id == identity)
                unit.session.add(
                    RetrievalSelectionRecord(
                        batch_id=batch.id,
                        source_key=f"skill:{identity}",
                        source_hash=item.candidate.document.content_hash,
                        text_hash=text_hash(""),
                        evidence={"reason": reason},
                        text=None,
                        omission_reason=reason,
                    )
                )
            if plan.selected is not None:
                selected = plan.selected
                candidate = selected.candidate
                decision = selected.applicability
                applicability = {
                    "status": decision.status,
                    "reasons": list(decision.reasons),
                    "missing_facts": list(decision.missing_facts),
                }
                unit.session.add(
                    RunSkillSelectionRecord(
                        run_id=run.id,
                        skill_version_id=candidate.document.version_id,
                        origin=candidate.origin,
                        trial_id=candidate.trial_id,
                        mode="retrieval",
                        rank=1,
                        score=selected.score,
                        query_terms=list(selected.matched_terms),
                        scope_key=content_hash(selector.scope.model_dump(mode="json")),
                        rendered_hash=text_hash(plan.text),
                        content_hash=candidate.document.content_hash,
                        applicability=applicability,
                        selection_policy_version=selector.VERSION,
                    )
                )
                unit.session.add(
                    RetrievalSelectionRecord(
                        batch_id=batch.id,
                        source_key=f"skill:{candidate.document.version_id}",
                        source_hash=candidate.document.content_hash,
                        text_hash=text_hash(plan.text),
                        text=plan.text,
                        evidence={"applicability": applicability},
                        rank=1,
                    )
                )
            owned.skill_selection_frozen = True
            await unit.events.append(
                run_id=run.id,
                event_type="skill.selected" if plan.selected else "skill.none_selected",
                payload={
                    "selector_version": selector.VERSION,
                    "origin": plan.selected.candidate.origin if plan.selected else None,
                    "applied": plan.selected is not None,
                    "degraded": batch.degraded,
                    "embedding_tokens": vectors.usage,
                },
                created_at=utc_now(),
            )
            await unit.session.flush()
            choice = await restore(selector, unit.session, batch)
        await unit.commit()
    await _verify_choice(selector, choice, run.id)
    return choice
