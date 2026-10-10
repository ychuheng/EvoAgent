"""Common formal eligibility reader; trial selection remains closed."""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal
from uuid import UUID

from sqlalchemy import select

from evoagent.db.models import (
    RunSkillSelectionRecord,
    SessionRecord,
    SkillRecord,
    SkillVersionRecord,
)
from evoagent.memory.schema import MemoryError
from evoagent.privacy.redaction import redact_value
from evoagent.skills.applicability import ApplicabilityDecision, SkillApplicabilityEvaluator
from evoagent.skills.canonical import content_hash
from evoagent.skills.schema import SkillDefinition
from evoagent.skills.selection_snapshot import SkillSelectionScope

if TYPE_CHECKING:
    from evoagent.skills.retrieval import SkillDocument


@dataclass(frozen=True)
class SkillCandidate:
    document: "SkillDocument"
    scope: SkillSelectionScope
    origin: Literal["formal", "trial", "pinned"]
    trial_id: UUID | None = None

    def __post_init__(self):
        if self.origin not in {"formal", "trial", "pinned"} or (
            (self.origin == "trial") != (self.trial_id is not None)
        ):
            raise ValueError("candidate trial identity invalid")
        if (
            content_hash(self.document.definition.model_dump(mode="json"))
            != self.document.content_hash
        ):
            raise ValueError("candidate content hash invalid")


@dataclass(frozen=True)
class RankedSkill:
    candidate: SkillCandidate
    score: float
    matched_terms: tuple[str, ...]
    applicability: ApplicabilityDecision


@dataclass(frozen=True)
class SkillSelectionPlan:
    selected: RankedSkill | None
    text: str | None
    omissions: tuple[tuple[UUID, str], ...] = ()


@dataclass(frozen=True)
class EligibleFormalSkill:
    version: SkillVersionRecord
    definition: SkillDefinition


class FormalSkillReader:
    async def check_run_bindings(self, session, *, task, run):
        # Pinned experiments have their own fenced source/target authorization.
        # This reader does not grant access to DRAFTs or trial bindings.
        if run.run_mode != "retrieval":
            return
        rows = list(
            await session.execute(
                select(SkillRecord, SkillVersionRecord)
                .join(SkillVersionRecord, SkillVersionRecord.skill_id == SkillRecord.id)
                .join(
                    RunSkillSelectionRecord,
                    RunSkillSelectionRecord.skill_version_id == SkillVersionRecord.id,
                )
                .where(RunSkillSelectionRecord.run_id == run.id)
            )
        )
        if not rows:
            return
        workspace_id = await session.scalar(
            select(SessionRecord.workspace_id).where(SessionRecord.id == task.session_id)
        )
        expected = {
            item["version_id"]: item["content_hash"]
            for item in (run.config_snapshot or {}).get("selected_skills", []) or []
        }
        for skill, version in rows:
            try:
                self.check_frozen_scope(
                    skill, workspace_id=workspace_id, project_id=task.project_id
                )
            except MemoryError:
                raise MemoryError("skill_source_revoked") from None
            if (
                version.lifecycle_status.value == "rejected"
                or content_hash(version.definition) != version.content_hash
                or str(version.id) in expected
                and expected[str(version.id)] != version.content_hash
                or redact_value(version.definition) != version.definition
            ):
                raise MemoryError("skill_source_revoked")

    @staticmethod
    def scope_allows(skill, *, workspace_id, project_id=None):
        # There is no implicit global sharing grant. DEFAULT_WORKSPACE_ID is a
        # real scope, not a wildcard. Formal sharing needs its own proven policy.
        return skill.workspace_id == workspace_id and skill.project_id in (None, project_id)

    @classmethod
    def check_frozen_scope(cls, skill, *, workspace_id, project_id=None):
        # A superseded active pointer must not silently change a frozen method.
        # Scope/explicit disable are still revocation boundaries on restore.
        if (
            skill is None
            or skill.status.value != "enabled"
            or not cls.scope_allows(skill, workspace_id=workspace_id, project_id=project_id)
        ):
            raise MemoryError("context_source_revoked")

    async def read(
        self,
        session,
        version_id,
        *,
        workspace_id=None,
        project_id=None,
        registry=None,
        max_risk="R1",
        facts=None,
        lock=False,
    ):
        version = await session.get(SkillVersionRecord, version_id)
        skill = await session.get(SkillRecord, version.skill_id) if version else None
        if skill is None or (
            workspace_id is not None
            and not self.scope_allows(skill, workspace_id=workspace_id, project_id=project_id)
        ):
            return None
        if lock:
            skill = await session.scalar(
                select(SkillRecord)
                .where(SkillRecord.id == skill.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            await session.refresh(version)
        if (
            skill.status.value != "enabled"
            or skill.active_version_id != version_id
            or version.lifecycle_status.value != "active"
            or workspace_id is not None
            and not self.scope_allows(skill, workspace_id=workspace_id, project_id=project_id)
        ):
            return None
        if content_hash(version.definition) != version.content_hash:
            raise MemoryError("retrieval_source_hash_mismatch")
        definition = SkillDefinition.model_validate(version.definition)
        if redact_value(version.definition) != version.definition:
            return None
        allowed = set(definition.preconditions.allowed_tools)
        if (
            "shell" in allowed
            or definition.preconditions.max_effective_risk.value > max_risk
            or registry is not None
            and not allowed <= set(registry.names)
            or facts is not None
            and SkillApplicabilityEvaluator().assess(definition, facts).status != "applicable"
        ):
            return None
        return EligibleFormalSkill(version, definition)


@dataclass(frozen=True)
class FrozenSkillChoice:
    matches: tuple
    text: str | None
    selections: tuple

    @property
    def selection_hash(self):
        from evoagent.skills.selection_snapshot import selection_hash

        return selection_hash(
            selector_version=SkillSelector.VERSION, renderer_version=2, selections=self.selections
        )


class SkillSelector:
    """Authorized personal baseline/pinned slice; ordinary/trial routing is pending."""

    VERSION = "skill-selector-v1"

    def __init__(self, factory, settings, registry, guard, scope, *, frozen_inputs=None):
        self.factory, self.settings, self.registry = factory, settings, registry
        self.guard, self.scope, self.inputs = guard, scope, frozen_inputs

    def assess(self, definition, facts):
        allowed = set(definition.preconditions.allowed_tools)
        if (
            "shell" in allowed
            or not allowed <= set(self.registry.names)
            or definition.preconditions.max_effective_risk.value
            > self.settings.skill_max_effective_risk.value
            or any(
                self.registry.get(name).risk.value
                > definition.preconditions.max_effective_risk.value
                for name in allowed
                if name in self.registry.names
            )
        ):
            return ApplicabilityDecision("inapplicable", ("tool_or_risk_unavailable",))
        return SkillApplicabilityEvaluator().assess(definition, facts)

    async def candidates(self, *, scope=None, run_mode="retrieval", pinned_version_id=None):
        from sqlalchemy import or_

        from evoagent.skills.access import SkillAccessError, SkillAccessPolicy
        from evoagent.skills.retrieval import SkillDocument
        from evoagent.skills.service import GateNotPassedError, SkillService
        from evoagent.skills.trials import SkillTrialService
        from evoagent.skills.usage import SkillUsageService

        if scope is not None and scope != self.scope:
            raise MemoryError("skill_selection_scope_invalid")
        if run_mode == "baseline":
            return ()
        if run_mode != "retrieval" or pinned_version_id is not None:
            # DRAFT pins require the existing fenced internal experiment path.
            raise MemoryError("skill_selection_internal_pin_required")
        async with self.factory() as session:
            skills = tuple(
                await session.scalars(
                    select(SkillRecord)
                    .where(
                        SkillRecord.workspace_id == self.scope.workspace_id,
                        SkillRecord.status == "enabled",
                        or_(
                            SkillRecord.project_id.is_(None),
                            SkillRecord.project_id == self.scope.project_id,
                        ),
                    )
                    .order_by(SkillRecord.id)
                    .limit(201)
                )
            )
        if len(skills) > 200:
            raise MemoryError("skill_catalogue_budget_exceeded")
        candidates = []
        for skill in skills:
            async with self.factory() as session:
                try:
                    trial = await self._trial_intent(session, skill.id)
                except MemoryError:
                    continue  # incomplete history cannot grant selection authority
                target = trial.version_id if trial else skill.active_version_id
                if target is None or trial is not None and trial.status != "active":
                    continue  # suspension does not silently restore the formal version
                if trial is not None:
                    ready = await SkillTrialService(self.factory)._assess(
                        session, trial.version_id, trial.validation_request_id
                    )
                    if not ready.ready or ready.report_hash != trial.report_hash:
                        continue
                try:
                    version = await SkillAccessPolicy().check(
                        session,
                        target,
                        workspace_id=self.scope.workspace_id,
                        project_id=self.scope.project_id,
                    )
                except SkillAccessError:
                    continue
                if trial is None:
                    if str(version.lifecycle_status) != "active":
                        continue
                    try:
                        await SkillService.check_formal_gate(session, version)
                    except GateNotPassedError:
                        continue
                candidate = SkillCandidate(
                    SkillDocument(
                        skill.id,
                        version.id,
                        SkillDefinition.model_validate(version.definition),
                        version.content_hash,
                    ),
                    self.scope,
                    "trial" if trial else "formal",
                    trial.id if trial else None,
                )
            if trial is not None:
                health = await SkillUsageService(self.factory).evaluate_trial_health(trial.id)
                if health.status != "healthy":
                    continue
            candidates.append(candidate)
        return tuple(candidates)

    async def _trial_intent(self, session, skill_id):
        from sqlalchemy import or_

        from evoagent.db.models import SkillTrialRecord

        trials = tuple(
            await session.scalars(
                select(SkillTrialRecord)
                .where(
                    SkillTrialRecord.skill_id == skill_id,
                    SkillTrialRecord.workspace_id == self.scope.workspace_id,
                    or_(
                        SkillTrialRecord.project_id.is_(None),
                        SkillTrialRecord.project_id == self.scope.project_id,
                    ),
                )
                .order_by(SkillTrialRecord.created_at.desc(), SkillTrialRecord.id.desc())
                .limit(201)
                .execution_options(populate_existing=True)
            )
        )
        if len(trials) > 200:
            raise MemoryError("skill_trial_history_budget_exceeded")
        return next(
            (item for item in trials if item.project_id == self.scope.project_id),
            trials[0] if trials else None,
        )

    async def revalidate(self, session, plan, *, scope=None):
        from evoagent.skills.access import SkillAccessError, SkillAccessPolicy
        from evoagent.skills.service import GateNotPassedError, SkillService
        from evoagent.skills.trials import SkillTrialService, TrialScope
        from evoagent.skills.usage import SkillUsageService

        if scope is not None and scope != self.scope:
            raise MemoryError("skill_selection_scope_invalid")
        if plan.selected is None:
            return
        candidate = plan.selected.candidate
        if candidate.scope != self.scope:
            raise MemoryError("skill_selection_scope_invalid")
        version = await session.get(SkillVersionRecord, candidate.document.version_id)
        if version is None:
            raise MemoryError("skill_source_revoked")
        skill = await session.scalar(
            select(SkillRecord)
            .where(SkillRecord.id == version.skill_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if skill is None:
            raise MemoryError("skill_source_revoked")
        await session.refresh(version)
        try:
            version = await SkillAccessPolicy().check(
                session,
                candidate.document.version_id,
                workspace_id=self.scope.workspace_id,
                project_id=self.scope.project_id,
            )
        except SkillAccessError:
            raise MemoryError("skill_source_revoked") from None
        intent = await self._trial_intent(session, skill.id)
        if version.content_hash != candidate.document.content_hash:
            raise MemoryError("skill_selection_changed")
        if candidate.origin == "formal":
            if (
                intent is not None
                or skill.active_version_id != version.id
                or str(version.lifecycle_status) != "active"
            ):
                raise MemoryError("skill_selection_changed")
            try:
                await SkillService.check_formal_gate(session, version)
            except GateNotPassedError:
                raise MemoryError("skill_formal_gate_unavailable") from None
        elif candidate.origin == "trial":
            trial = intent
            if (
                trial is None
                or trial.id != candidate.trial_id
                or trial.status != "active"
                or trial.version_id != version.id
                or trial.workspace_id != self.scope.workspace_id
                or trial.project_id not in (None, self.scope.project_id)
                or trial.scope_key != TrialScope(trial.workspace_id, trial.project_id).key
            ):
                raise MemoryError("skill_selection_changed")
            ready = await SkillTrialService(self.factory)._assess(
                session, trial.version_id, trial.validation_request_id
            )
            if not ready.ready or ready.report_hash != trial.report_hash:
                raise MemoryError("skill_trial_validation_revoked")
            health = await SkillUsageService(self.factory).health_snapshot(session, trial)
            if health.status != "healthy":
                raise MemoryError("skill_trial_health_unavailable")
        else:
            raise MemoryError("skill_selection_internal_pin_required")

    async def rank(self, goal, candidates, *, facts, backend="bm25", distances=None):
        from evoagent.retrieval.hybrid import rank
        from evoagent.skills.retrieval import BM25Retriever

        if backend not in {"bm25", "hybrid"}:
            raise ValueError("selector backend invalid")
        eligible = {}
        for candidate in candidates:
            if candidate.scope != self.scope:
                continue
            decision = self.assess(candidate.document.definition, facts)
            if decision.status == "applicable":
                if candidate.document.version_id in eligible:
                    raise ValueError("duplicate selection candidate")
                eligible[candidate.document.version_id] = candidate, decision
        if backend == "bm25":
            matches = BM25Retriever().search(
                goal, tuple(item[0].document for item in eligible.values())
            )
            return tuple(
                RankedSkill(
                    eligible[match.document.version_id][0],
                    match.score,
                    match.matched_terms,
                    eligible[match.document.version_id][1],
                )
                for match in matches
                if match.score >= self.settings.skill_retrieval_min_score
            )
        rows = rank(
            goal,
            {key: value[0].document.text for key, value in eligible.items()},
            distances or {},
            minimum_score=self.settings.skill_retrieval_min_score,
            maximum_distance=self.settings.retrieval_max_vector_distance,
            rrf_k=self.settings.retrieval_rrf_k,
        )
        return tuple(
            RankedSkill(
                eligible[key][0],
                evidence["rrf"],
                tuple(evidence.get("terms", ())),
                eligible[key][1],
            )
            for key, evidence in rows
        )

    def choose(self, ranked, *, token_budget, max_skills=1):
        from evoagent.core.context_budget import ConservativeTokenCounter
        from evoagent.core.models import Message, MessageRole, ModelRequest
        from evoagent.skills.rendering import SkillContextRenderer

        if (
            type(token_budget) is not int
            or token_budget < 0
            or type(max_skills) is not int
            or max_skills not in {0, 1}
        ):
            raise ValueError("selector choice budget invalid")
        if max_skills == 0:
            return SkillSelectionPlan(None, None)
        omissions = []
        for item in ranked:
            text = SkillContextRenderer(2).render(item.candidate.document.definition)
            count = (
                ConservativeTokenCounter()
                .count_request(
                    ModelRequest(
                        model="selection-budget",
                        messages=(Message(role=MessageRole.USER, content=text),),
                    )
                )
                .count
            )
            if count <= token_budget:
                return SkillSelectionPlan(item, text, tuple(omissions))
            omissions.append((item.candidate.document.version_id, "partition_budget"))
        return SkillSelectionPlan(None, None, tuple(omissions))

    async def _target(self, unit, task, run):
        from evoagent.runtime.run_config import RunMode
        from evoagent.skills.access import SkillAccessError, SkillAccessPolicy

        if (
            self.guard.lease.run_id != run.id
            or self.guard.lease.task_id != task.id
            or run.task_id != task.id
        ):
            raise MemoryError("skill_selection_scope_invalid")
        await self.guard.check(unit.session)
        owned_task = await unit.tasks.get(task.id)
        owned_run = await unit.runs.get(run.id)
        chat = await unit.session.get(SessionRecord, owned_task.session_id)
        from evoagent.learning.project_validation import selection_project_matches

        project_matches = await selection_project_matches(
            unit.session, owned_run, owned_task, self.scope, factory=self.factory
        )
        if (
            owned_run.data_role != "dev"
            or owned_run.run_mode not in {RunMode.BASELINE.value, RunMode.PINNED_SKILL.value}
            or chat is None
            or chat.workspace_id != self.scope.workspace_id
            or not project_matches
            or owned_task.goal != task.goal
            or owned_task.family != task.family
            or owned_run.pinned_skill_version_id != run.pinned_skill_version_id
            or owned_run.run_mode != run.run_mode
        ):
            raise MemoryError("skill_selection_scope_invalid")
        target = owned_run.pinned_skill_version_id
        if (owned_run.run_mode == RunMode.PINNED_SKILL.value) != (target is not None):
            raise MemoryError("skill_selection_scope_invalid")
        version = None
        if target is not None:
            try:
                version = await SkillAccessPolicy().check(
                    unit.session,
                    target,
                    workspace_id=self.scope.workspace_id,
                    project_id=self.scope.project_id,
                )
            except SkillAccessError:
                raise MemoryError("skill_source_revoked") from None
        return owned_run, chat, version

    async def resolve_pinned(self, task, run):
        from evoagent.core.context_budget import ConservativeTokenCounter
        from evoagent.core.models import Message, MessageRole, ModelRequest
        from evoagent.db.models import RetrievalBatchRecord, RetrievalSelectionRecord, utc_now
        from evoagent.db.unit_of_work import UnitOfWork
        from evoagent.runtime.checkpoints import SnapshotCompatibilityError
        from evoagent.sessions.service import text_hash
        from evoagent.skills.applicability import VerifiedSkillFacts
        from evoagent.skills.rendering import SkillContextRenderer

        facts = VerifiedSkillFacts.from_runtime(self.registry, self.inputs, task_family=task.family)
        config = {
            "selector_version": self.VERSION,
            "renderer_version": 2,
            "scope": self.scope.model_dump(mode="json"),
            "mode": run.run_mode,
            "target": str(run.pinned_skill_version_id) if run.pinned_skill_version_id else None,
            "facts_hash": content_hash(facts.as_mapping()),
            "top_k": min(self.settings.skill_retrieval_top_k, 1),
            "max_risk": self.settings.skill_max_effective_risk.value,
            "skill_budget": self.settings.retrieval_skill_budget,
        }
        async with UnitOfWork(self.factory) as unit:
            owned, chat, version = await self._target(unit, task, run)
            saved = await self._saved(unit.session, run.id)
            if saved is not None:
                self._check_config(saved, config, task.goal)
                choice = await self._restore(unit.session, saved, version)
            else:
                if owned.skill_selection_frozen or owned.config_snapshot is not None:
                    raise SnapshotCompatibilityError("legacy selection cannot be upgraded in place")
                choice = None
                definition = SkillDefinition.model_validate(version.definition) if version else None
                source_hash = version.content_hash if version else None
            # Release fencing locks before scanner IO or refusal-event writes.
            await unit.commit()
        if choice is not None:
            if choice.text is not None:
                await self._verify_text(choice.text, run.id, choice.selections[0].version_id)
            return choice
        text, evidence, reason = None, None, None
        if definition is not None:
            decision = self.assess(definition, facts)
            evidence = {
                "status": decision.status,
                "reasons": list(decision.reasons),
                "missing_facts": list(decision.missing_facts),
            }
            reason = None if evidence["status"] == "applicable" else evidence["status"]
            if config["top_k"] == 0:
                reason = "top_k"
            if reason is None:
                text = SkillContextRenderer(2).render(definition)
                cost = (
                    ConservativeTokenCounter()
                    .count_request(
                        ModelRequest(
                            model=run.model,
                            messages=(Message(role=MessageRole.USER, content=text),),
                        )
                    )
                    .count
                )
                if cost > config["skill_budget"]:
                    reason, text = "partition_budget", None
                else:
                    await self._verify_text(text, run.id, run.pinned_skill_version_id)
        async with UnitOfWork(self.factory) as unit:
            owned, chat, version = await self._target(unit, task, run)
            if (version.content_hash if version else None) != source_hash:
                raise MemoryError("skill_source_revoked")
            saved = await self._saved(unit.session, run.id)
            if saved is not None:
                self._check_config(saved, config, task.goal)
                choice = await self._restore(unit.session, saved, version)
            else:
                if owned.skill_selection_frozen or owned.config_snapshot is not None:
                    raise SnapshotCompatibilityError("selection changed during preparation")
                batch = RetrievalBatchRecord(
                    run_id=run.id,
                    purpose="skill_selector",
                    query_hash=text_hash(task.goal),
                    workspace_id=chat.workspace_id,
                    session_id=chat.id,
                    config=config,
                    selected_count=int(text is not None),
                )
                unit.session.add(batch)
                await unit.session.flush()
                if version is not None:
                    if text is not None:
                        unit.session.add(
                            RunSkillSelectionRecord(
                                run_id=run.id,
                                skill_version_id=version.id,
                                origin="pinned",
                                mode=run.run_mode,
                                rank=1,
                                score=1,
                                query_terms=["pinned"],
                                scope_key=content_hash(self.scope.model_dump(mode="json")),
                                rendered_hash=text_hash(text),
                                content_hash=version.content_hash,
                                applicability=evidence,
                                selection_policy_version=self.VERSION,
                            )
                        )
                    unit.session.add(
                        RetrievalSelectionRecord(
                            batch_id=batch.id,
                            source_key=f"skill:{version.id}",
                            source_hash=version.content_hash,
                            text_hash=text_hash(text or ""),
                            text=text,
                            evidence=evidence,
                            rank=1 if text is not None else None,
                            omission_reason=reason,
                        )
                    )
                owned.skill_selection_frozen = True
                await unit.events.append(
                    run_id=run.id,
                    event_type="skill.selected" if text is not None else "skill.none_selected",
                    payload={
                        "selector_version": self.VERSION,
                        "target": config["target"],
                        "applied": text is not None,
                    },
                    created_at=utc_now(),
                )
                await unit.session.flush()
                choice = await self._restore(unit.session, batch, version)
            await unit.commit()
        if choice.text is not None:
            await self._verify_text(choice.text, run.id, choice.selections[0].version_id)
        return choice

    @staticmethod
    async def _saved(session, run_id):
        from evoagent.db.models import RetrievalBatchRecord

        return await session.scalar(
            select(RetrievalBatchRecord).where(
                RetrievalBatchRecord.run_id == run_id,
                RetrievalBatchRecord.purpose == "skill_selector",
            )
        )

    @staticmethod
    def _check_config(batch, config, goal):
        from evoagent.runtime.checkpoints import SnapshotCompatibilityError
        from evoagent.sessions.service import text_hash

        if batch.config != config or batch.query_hash != text_hash(goal):
            raise SnapshotCompatibilityError("frozen selector configuration changed")

    async def _verify_text(self, text, run_id, version_id):
        from evoagent.privacy.artifact_access import ArtifactInjectionGuard
        from evoagent.sessions.service import text_hash
        from evoagent.tools.base import ToolError

        try:
            await ArtifactInjectionGuard(
                session_factory=self.factory, settings=self.settings
            ).verify_derived_text(
                text=text,
                run_id=run_id,
                source_id=f"skill:{version_id}",
                source_hash=text_hash(text),
                purpose="frozen_selection",
            )
        except ToolError:
            raise MemoryError("context_source_blocked") from None

    async def _restore(self, session, batch, version):
        from evoagent.db.models import RetrievalSelectionRecord
        from evoagent.sessions.service import text_hash
        from evoagent.skills.retrieval import RetrievalMatch, SkillDocument
        from evoagent.skills.selection_snapshot import SkillSelectionSnapshot

        bindings = list(
            await session.scalars(
                select(RunSkillSelectionRecord).where(
                    RunSkillSelectionRecord.run_id == batch.run_id
                )
            )
        )
        rows = list(
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
        if batch.selected_count != 1 or len(rows) != 1 or len(bindings) != 1 or version is None:
            raise MemoryError("skill_selection_corrupt")
        row, binding = rows[0], bindings[0]
        if (
            row.text is None
            or row.source_key != f"skill:{version.id}"
            or row.source_hash != version.content_hash
            or text_hash(row.text) != row.text_hash
            or binding.skill_version_id != version.id
            or binding.content_hash != version.content_hash
            or binding.rendered_hash != row.text_hash
            or binding.origin != "pinned"
            or str(binding.mode) != "pinned_skill"
            or binding.rank != 1
            or binding.trial_id is not None
            or row.rank != 1
            or binding.selection_policy_version != self.VERSION
            or binding.scope_key != content_hash(self.scope.model_dump(mode="json"))
            or binding.applicability != row.evidence
            or row.evidence.get("status") != "applicable"
        ):
            raise MemoryError("skill_selection_corrupt")
        definition = SkillDefinition.model_validate(version.definition)
        match = RetrievalMatch(
            SkillDocument(version.skill_id, version.id, definition, version.content_hash),
            1,
            ("pinned",),
        )
        frozen = SkillSelectionSnapshot(
            version_id=version.id,
            content_hash=version.content_hash,
            rendered_hash=row.text_hash,
            origin="pinned",
            scope=self.scope,
        )
        return FrozenSkillChoice((match,), row.text, (frozen,))
