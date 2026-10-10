"""Common formal eligibility reader; trial selection remains closed."""

from dataclasses import dataclass

from sqlalchemy import select

from evoagent.db.models import (
    RunSkillSelectionRecord,
    SessionRecord,
    SkillRecord,
    SkillVersionRecord,
)
from evoagent.memory.schema import MemoryError
from evoagent.privacy.redaction import redact_value
from evoagent.skills.applicability import SkillApplicabilityEvaluator
from evoagent.skills.canonical import content_hash
from evoagent.skills.schema import SkillDefinition


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
        if (
            owned_run.data_role != "dev"
            or owned_run.run_mode not in {RunMode.BASELINE.value, RunMode.PINNED_SKILL.value}
            or chat is None
            or chat.workspace_id != self.scope.workspace_id
            or owned_task.project_id != self.scope.project_id
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
            decision = SkillApplicabilityEvaluator().assess(definition, facts)
            evidence = {
                "status": decision.status,
                "reasons": list(decision.reasons),
                "missing_facts": list(decision.missing_facts),
            }
            allowed = set(definition.preconditions.allowed_tools)
            if (
                "shell" in allowed
                or not allowed <= set(self.registry.names)
                or definition.preconditions.max_effective_risk.value > config["max_risk"]
                or any(
                    self.registry.get(name).risk.value
                    > definition.preconditions.max_effective_risk.value
                    for name in allowed
                    if name in self.registry.names
                )
            ):
                evidence = {
                    "status": "inapplicable",
                    "reasons": ["tool_or_risk_unavailable"],
                    "missing_facts": [],
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
