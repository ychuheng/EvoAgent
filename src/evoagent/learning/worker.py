"""Resumable personal candidate stages; no trial activation in this handler."""

import asyncio
from uuid import UUID

from sqlalchemy import func, or_, select, update

from evoagent.db.models import (
    ArtifactRecord,
    LearningPolicyRecord,
    LearningRequestRecord,
    LearningSourceRecord,
    LearningSpendReservationRecord,
    MaintenanceJobRecord,
    ProjectRecord,
    RunFeedbackRecord,
    RunRecord,
    SkillRecord,
    SkillVersionRecord,
    TaskRecord,
)
from evoagent.db.repositories.events import RunEventRepository
from evoagent.learning.jobs import LearningJobGuard
from evoagent.learning.planner import SkillEvolutionPlanner
from evoagent.learning.revision_aggregation import (
    aggregation_refs,
    lock_aggregation_runs,
    verify_aggregation,
)
from evoagent.learning.schema import LearningError
from evoagent.learning.sources import PersonalSourceService
from evoagent.skills.access import SkillAccessError, SkillAccessPolicy
from evoagent.skills.canonical import content_hash
from evoagent.skills.extraction import SkillExtractionService, require_s6_annotations
from evoagent.skills.provenance import FrozenSkillSource
from evoagent.skills.schema import SkillDefinition
from evoagent.tasks.lease_guard import database_now
from evoagent.workers.background import (
    BackgroundLease,
    MaintenanceLeaseGuard,
    finish_in_transaction,
)


class LearningJobHandler:
    def __init__(
        self,
        factory,
        store,
        generator_factory,
        validator,
        *,
        learning_enabled=False,
        budget=None,
        code_version="0.4.0.dev0",
    ):
        self.factory, self.store = factory, store
        self.generator_factory, self.validator = generator_factory, validator
        self.enabled, self.budget = learning_enabled, budget
        self.code_version = code_version
        self.planner = SkillEvolutionPlanner()

    async def execute(self, job_id, owner, epoch):
        lease = BackgroundLease(job_id, owner, epoch)
        async with self.factory() as session:
            job = await session.get(MaintenanceJobRecord, job_id)
            kind, request_id = job.kind, job.learning_request_id
        if kind == "learning_revoke":
            return await self.revoke_sources(lease)
        if kind == "learning_validation_completed":
            return await self.enqueue_collection(lease)
        if kind == "learning_budget_reconcile":
            changed = await self.budget.reconcile_stale() if self.budget else 0
            async with self.factory() as session:
                await finish_in_transaction(session, lease, {"reconciled": changed})
                await session.commit()
            return
        if kind not in {"learning_propose", "learning_validate"} or request_id is None:
            raise LearningError("unsupported_learning_job")
        guard = LearningJobGuard(job_id, owner, epoch)
        try:
            async with self.factory() as session:
                _, request = await guard.check(session)
                stage = request.stage
            if stage == "prepare":
                await self.prepare(request_id, guard)
            elif stage == "generate":
                await self.propose(request_id, guard)
            elif stage == "static_validate":
                await self.validate(request_id, guard)
            elif stage in {"task_validate", "waiting_validation"}:
                from evoagent.evals.validators import default_validator_registry
                from evoagent.learning.validation_execution import PersonalValidationExecution

                execution = PersonalValidationExecution(
                    self.factory,
                    self.store,
                    default_validator_registry(),
                    self._check,
                    settings=self.budget.settings if self.budget else None,
                )

                async def complete(session, guard, request, next_stage, **result):
                    await self._advance(
                        session,
                        guard,
                        request,
                        next_stage,
                        status="ready_for_review"
                        if next_stage == "validation_review"
                        else "running",
                        queue_next=False,
                        result=result,
                    )

                if stage == "task_validate":
                    await execution.start(
                        request_id, guard, complete, code_version=self.code_version
                    )
                else:
                    await execution.collect(request_id, guard, complete)
            else:
                raise LearningError("invalid_learning_stage")
        except LearningError as error:
            await self.fail(guard, error.code)
        except asyncio.CancelledError:
            raise  # shutdown keeps the durable stage for lease takeover
        except Exception as error:
            # No raw provider response, evidence or exception string in errors.
            await self.fail(guard, "learning_" + type(error).__name__)

    def source_service(self, request):
        return PersonalSourceService(
            self.factory,
            max_source_risk=request.policy_snapshot["max_source_risk"],
            artifact_store=self.store,
        )

    async def _check(self, session, guard, *, source_required=False):
        found = await session.get(MaintenanceJobRecord, guard.job_id)
        initial = await session.get(LearningRequestRecord, found.learning_request_id)
        sources = self.source_service(initial)
        await lock_aggregation_runs(session, initial)
        await sources._lock_run_scope(session, initial.origin_run_id)
        source = await session.scalar(
            select(LearningSourceRecord)
            .where(
                LearningSourceRecord.run_id == initial.origin_run_id,
                LearningSourceRecord.source_revision == initial.frozen_inputs["source_revision"],
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        job, request = await guard.check(session)
        policy = await session.get(LearningPolicyRecord, request.workspace_id)
        if not self.enabled or policy is None or policy.mode == "off":
            raise LearningError("learning_policy_off")
        if request.trigger == "discover" and policy.mode != "suggest":
            raise LearningError("learning_discovery_not_authorized")
        sources.max_source_risk = min(sources.max_source_risk, policy.max_source_risk)
        await verify_aggregation(session, request, sources)
        await sources.check_in_session(
            session,
            request.origin_run_id,
            UUID(request.frozen_inputs["feedback_id"])
            if request.frozen_inputs.get("feedback_id")
            else None,
            "feedback" if request.frozen_inputs.get("feedback_id") else "manual",
        )
        if source_required:
            artifact = await session.get(ArtifactRecord, source.artifact_id) if source else None
            if (
                source is None
                or source.status != "valid"
                or artifact is None
                or artifact.attributes.get("erased")
                or artifact.redaction_status == "quarantined"
                or artifact.content_hash != source.content_hash
            ):
                raise LearningError("learning_source_revoked")
        return job, request, source

    async def _advance(
        self, session, guard, request, stage, *, status="running", result=None, queue_next=True
    ):
        request.stage, request.status = stage, status
        request.error_code = None
        request.lock_version += 1
        if status == "running" and queue_next:
            session.add(
                MaintenanceJobRecord(
                    dedupe_key=f"learning:{request.id}:{stage}:{request.lock_version}",
                    kind="learning_validate" if stage == "static_validate" else "learning_propose",
                    learning_request_id=request.id,
                    payload={
                        "request_id": str(request.id),
                        "stage": stage,
                        "request_lock_version": request.lock_version,
                    },
                )
            )
        await finish_in_transaction(
            session,
            BackgroundLease(guard.job_id, guard.owner, guard.epoch),
            {"stage": stage, **(result or {})},
        )
        await RunEventRepository(session).append(
            run_id=request.origin_run_id,
            event_type="learning.stage_completed",
            payload={"request_id": str(request.id), "stage": stage, "status": status},
            created_at=await database_now(session),
        )

    async def enqueue_collection(self, lease):
        from evoagent.db.models import EvalExperimentRecord
        from evoagent.learning.service import LearningService

        async with self.factory() as session:
            found = await session.get(MaintenanceJobRecord, lease.job_id)
            request_id, experiment_id = (
                UUID(found.payload["request_id"]),
                UUID(found.payload["experiment_id"]),
            )
            # Run -> Workspace -> Request -> Job. Never hold the experiment
            # or validation Task lock while acquiring the origin request lock.
            request = await LearningService(self.factory)._locked_request(session, request_id, None)
            await MaintenanceLeaseGuard(lease).check(session)
            experiment = await session.get(EvalExperimentRecord, experiment_id)
            if (
                experiment is None
                or str(experiment.status) != "completed"
                or experiment.purpose != "personal_validation"
                or experiment.learning_request_id != request.id
                or request.validation_experiment_id != experiment.id
            ):
                raise LearningError("validation_completion_identity_invalid")
            queued = request.status == "running" and request.stage == "waiting_validation"
            if queued:
                dedupe = f"learning:{request.id}:waiting_validation:{request.lock_version}"
                if not await session.scalar(
                    select(MaintenanceJobRecord.id).where(MaintenanceJobRecord.dedupe_key == dedupe)
                ):
                    session.add(
                        MaintenanceJobRecord(
                            dedupe_key=dedupe,
                            kind="learning_validate",
                            learning_request_id=request.id,
                            payload={
                                "request_id": str(request.id),
                                "stage": "waiting_validation",
                                "request_lock_version": request.lock_version,
                            },
                        )
                    )
            await finish_in_transaction(session, lease, {"collection_queued": queued})
            await session.commit()

    async def prepare(self, request_id, guard):
        async with self.factory() as session:
            _, request, _ = await self._check(session, guard)
        await self.source_service(request).freeze(
            request.origin_run_id,
            UUID(request.frozen_inputs["feedback_id"])
            if request.frozen_inputs.get("feedback_id")
            else None,
            source_revision=request.frozen_inputs["source_revision"],
            job_guard=guard,
        )
        async with self.factory() as session:
            _, request, source = await self._check(session, guard, source_required=True)
            await self._advance(
                session, guard, request, "generate", result={"source_id": str(source.id)}
            )
            await session.commit()

    async def _watch(self, guard, source_id, source_epoch):
        while True:
            await asyncio.sleep(0.2)
            async with self.factory() as session:
                record = (
                    await session.execute(
                        select(
                            MaintenanceJobRecord,
                            LearningRequestRecord,
                            LearningSourceRecord,
                            LearningPolicyRecord,
                            ArtifactRecord,
                            TaskRecord,
                            ProjectRecord,
                            func.current_timestamp(),
                        )
                        .join(
                            LearningRequestRecord,
                            LearningRequestRecord.id == MaintenanceJobRecord.learning_request_id,
                        )
                        .join(LearningSourceRecord, LearningSourceRecord.id == source_id)
                        .join(
                            LearningPolicyRecord,
                            LearningPolicyRecord.workspace_id == LearningRequestRecord.workspace_id,
                        )
                        .join(ArtifactRecord, ArtifactRecord.id == LearningSourceRecord.artifact_id)
                        .join(RunRecord, RunRecord.id == LearningSourceRecord.run_id)
                        .join(TaskRecord, TaskRecord.id == RunRecord.task_id)
                        .outerjoin(ProjectRecord, ProjectRecord.id == TaskRecord.project_id)
                        .where(MaintenanceJobRecord.id == guard.job_id)
                    )
                ).first()
                if record is None:
                    raise LearningError("learning_dispatch_cancelled")
                job, request, source, policy, artifact, task, project, now = record
                from datetime import UTC

                expiry = job.lease_expires_at
                if expiry is not None and expiry.tzinfo is None:
                    expiry = expiry.replace(tzinfo=UTC)
                if now.tzinfo is None:
                    now = now.replace(tzinfo=UTC)
                if (
                    job is None
                    or job.lease_owner != guard.owner
                    or job.lease_epoch != guard.epoch
                    or job.status != "running"
                    or job.cancel_requested
                    or request is None
                    or request.status not in {"queued", "running"}
                    or request.lock_version != job.payload.get("request_lock_version")
                    or source is None
                    or source.status != "valid"
                    or source.revocation_epoch != source_epoch
                    or policy is None
                    or policy.mode == "off"
                    or expiry is None
                    or expiry <= now
                    or artifact.attributes.get("erased")
                    or artifact.redaction_status == "quarantined"
                    or (
                        task.project_id is not None
                        and (
                            project is None
                            or str(project.status) != "available"
                            or project.authorization_version != task.project_authorization_version
                        )
                    )
                ):
                    raise LearningError("learning_dispatch_cancelled")
                refs = aggregation_refs(request)
                if refs:
                    # One bounded metadata query for the whole aggregation;
                    # never replay bodies or rebuild every manifest on each tick.
                    latest = (
                        select(RunFeedbackRecord.id)
                        .where(RunFeedbackRecord.run_id == LearningSourceRecord.run_id)
                        .order_by(RunFeedbackRecord.revision.desc())
                        .limit(1)
                        .correlate(LearningSourceRecord)
                        .scalar_subquery()
                    )
                    rows = (
                        await session.execute(
                            select(
                                LearningSourceRecord,
                                ArtifactRecord,
                                TaskRecord,
                                ProjectRecord,
                                RunFeedbackRecord,
                                SkillRecord.lock_version,
                            )
                            .join(
                                ArtifactRecord,
                                ArtifactRecord.id == LearningSourceRecord.artifact_id,
                            )
                            .join(RunRecord, RunRecord.id == LearningSourceRecord.run_id)
                            .join(TaskRecord, TaskRecord.id == RunRecord.task_id)
                            .outerjoin(ProjectRecord, ProjectRecord.id == TaskRecord.project_id)
                            .join(RunFeedbackRecord, RunFeedbackRecord.id == latest)
                            .join(SkillRecord, SkillRecord.id == request.target_skill_id)
                            .where(LearningSourceRecord.id.in_([r.source_id for r in refs]))
                        )
                    ).all()
                    expected = {r.source_id: r for r in refs}
                    if len(rows) != len(refs) or policy.mode != "suggest":
                        raise LearningError("learning_dispatch_cancelled")
                    for src, art, task, project, feedback, target_lock in rows:
                        ref = expected[src.id]
                        if (
                            src.status != "valid"
                            or src.revocation_epoch != ref.revocation_epoch
                            or src.content_hash != ref.content_hash
                            or art.content_hash != ref.content_hash
                            or art.attributes.get("erased")
                            or art.redaction_status == "quarantined"
                            or feedback.id != ref.feedback_id
                            or not feedback.learn_from_feedback
                            or target_lock
                            != request.policy_snapshot["revision_aggregation"][
                                "target_lock_version"
                            ]
                            or (
                                task.project_id is not None
                                and (
                                    project is None
                                    or str(project.status) != "available"
                                    or project.authorization_version
                                    != task.project_authorization_version
                                )
                            )
                        ):
                            raise LearningError("learning_dispatch_cancelled")

    async def _generate(self, generator, source, context, guard):
        sources = source if isinstance(source, tuple) else (source,)
        work = asyncio.create_task(generator.generate(sources, context=context))
        watches = [
            asyncio.create_task(
                self._watch(
                    guard,
                    item.learning_source_id,
                    context.get("source_epochs", {}).get(
                        str(item.learning_source_id), context["source_revocation_epoch"]
                    ),
                )
            )
            # The primary watch also inspects all aggregation metadata in one query.
            for item in sources[:1]
        ]
        try:
            done, _ = await asyncio.wait((work, *watches), return_when=asyncio.FIRST_COMPLETED)
            for watch in watches:
                if watch in done:
                    await watch
            return await work
        finally:
            for task in (work, *watches):
                task.cancel()
            await asyncio.gather(work, *watches, return_exceptions=True)

    async def propose(self, request_id, guard):
        async with self.factory() as session:
            _, request, source = await self._check(session, guard, source_required=True)
            skill = (
                await session.get(SkillRecord, request.target_skill_id)
                if request.target_skill_id
                else None
            )
            base = (
                await session.get(SkillVersionRecord, request.base_version_id)
                if request.base_version_id
                else None
            )
            self.planner.validate_target(request, skill, base)
            if base is not None:
                try:
                    await SkillAccessPolicy().check(
                        session,
                        base.id,
                        workspace_id=request.workspace_id,
                        project_id=request.project_id,
                    )
                except SkillAccessError as error:
                    raise LearningError("revision_source_unavailable") from error
            context = {
                "target_skill_id": str(skill.id) if skill else None,
                "required_name": skill.slug if skill else None,
                "base_definition": base.definition if base else None,
                "source_revocation_epoch": source.revocation_epoch,
                "tool_catalog": [
                    item.model_dump(mode="json") for item in self.validator._registry.definitions()
                ],
                "allowed_tools": sorted(self.validator._allowed_tools),
                "max_risk": self.validator._max_risk.value,
                "instruction": (
                    "仅提炼可迁移方法，修订必须保持 required_name。"
                    "不得复制答案、私人绝对路径或凭据。"
                    "必须提供停止条件、成功标准及标明假设的反例。"
                ),
            }
            records = await verify_aggregation(session, request, self.source_service(request))
            records = records or (source,)
            if aggregation_refs(request):
                context["source_epochs"] = {str(item.id): item.revocation_epoch for item in records}
        frozen = []
        async with asyncio.timeout(10):
            if aggregation_refs(request):
                await self._verify_aggregation_base(request)
            for item in records:
                evidence = await self.source_service(request).read_frozen(
                    item.id, expected_revocation_epoch=item.revocation_epoch
                )
                frozen.append(
                    FrozenSkillSource(
                        eval_run_id=None,
                        run_id=item.run_id,
                        artifact_id=item.artifact_id,
                        source_trace_hash=item.content_hash,
                        payload=evidence.model_dump(mode="json"),
                        source_kind="personal",
                        learning_source_id=item.id,
                    )
                )
        frozen = tuple(frozen)
        generator = self.generator_factory(request, guard)
        async with self.factory() as session:
            await self._check(session, guard, source_required=True)
        definition = await self._generate(generator, frozen, context, guard)
        if aggregation_refs(request):
            async with asyncio.timeout(10):
                await self._verify_aggregation_base(request)
                for item in records:
                    await self.source_service(request).read_frozen(
                        item.id, expected_revocation_epoch=item.revocation_epoch
                    )
        if definition.schema_version != 2:
            raise LearningError("personal_candidate_v2_required")
        # Scan and DSL validation run outside database transactions and off-loop.
        await asyncio.to_thread(self.validator.validate, definition)
        require_s6_annotations(definition)
        digest = content_hash(definition.model_dump(mode="json"))
        async with self.factory() as session:
            _, request, source = await self._check(session, guard, source_required=True)
            skill = (
                await session.get(SkillRecord, request.target_skill_id)
                if request.target_skill_id
                else None
            )
            base = (
                await session.get(SkillVersionRecord, request.base_version_id)
                if request.base_version_id
                else None
            )
            self.planner.validate_target(request, skill, base)
            if base is not None:
                try:
                    await SkillAccessPolicy().check(
                        session,
                        base.id,
                        workspace_id=request.workspace_id,
                        project_id=request.project_id,
                    )
                except SkillAccessError as error:
                    raise LearningError("revision_source_unavailable") from error
            if skill and definition.name != skill.slug:
                raise LearningError("revision_name_mismatch")
            existing = list(
                await session.scalars(
                    select(SkillVersionRecord)
                    .join(SkillRecord, SkillRecord.id == SkillVersionRecord.skill_id)
                    .where(
                        SkillRecord.workspace_id == request.workspace_id,
                        or_(
                            SkillRecord.project_id.is_(None),
                            SkillRecord.project_id == request.project_id,
                        ),
                        SkillVersionRecord.content_hash == digest,
                    )
                    .limit(33)
                )
            )
            eligible = []
            for candidate in existing[:32]:
                try:
                    await SkillAccessPolicy().check(
                        session,
                        candidate.id,
                        workspace_id=request.workspace_id,
                        project_id=request.project_id,
                    )
                except SkillAccessError:
                    continue
                eligible.append(candidate)
            decision = self.planner.suggest(definition, eligible, revising=skill is not None)
            if decision.action == "duplicate":
                await self._advance(
                    session,
                    guard,
                    request,
                    "duplicate",
                    status="skipped",
                    result={"duplicate_version_id": str(decision.duplicate_version_id)},
                )
                await session.commit()
                return

            async def complete_stage(delivery_session, delivery_request, version):
                delivery_request.candidate_version_id = version.id
                await self._advance(
                    delivery_session,
                    guard,
                    delivery_request,
                    "static_validate",
                    result={"candidate_version_id": str(version.id)},
                )

            await SkillExtractionService(
                self.factory,
                None,
                generator,
                self.validator,
            ).extract_sources(
                frozen,
                request_id=request.id,
                target_skill_id=request.target_skill_id,
                base_version_id=request.base_version_id,
                workspace_id=request.workspace_id,
                project_id=request.project_id,
                job_guard=guard,
                definition=definition,
                context=context,
                session=session,
                complete_stage=complete_stage,
            )
            await session.commit()

    async def _verify_aggregation_base(self, request):
        from evoagent.config import Settings
        from evoagent.skills.source_verification import SkillSourceVerifier
        from evoagent.skills.trials import TrialScope

        await SkillSourceVerifier(
            self.factory,
            self.budget.settings if self.budget else Settings(_env_file=None),
            TrialScope(request.workspace_id, request.project_id),
            store=self.store,
        ).verify(request.base_version_id)

    async def validate(self, request_id, guard):
        async with self.factory() as session:
            _, request, source = await self._check(session, guard, source_required=True)
            version = await session.get(SkillVersionRecord, request.candidate_version_id)
            if version is None or content_hash(version.definition) != version.content_hash:
                raise LearningError("candidate_identity_invalid")
            definition = SkillDefinition.model_validate(version.definition)
            records = await verify_aggregation(session, request, self.source_service(request))
            records = records or (source,)
        async with asyncio.timeout(10):
            for item in records:
                await self.source_service(request).read_frozen(
                    item.id, expected_revocation_epoch=item.revocation_epoch
                )
        result = await asyncio.to_thread(self.validator.validate, definition)
        require_s6_annotations(definition)
        report = {
            "schema_version": 1,
            "validation_mode": "static_only",
            "candidate_version_id": str(version.id),
            "candidate_hash": version.content_hash,
            "source_id": str(source.id),
            "source_hash": source.content_hash,
            "static_validation": {
                "passed": True,
                "ordered_step_ids": list(result.ordered_step_ids),
            },
            "task_validation": {"status": "not_run"},
            "trial_eligible": False,
        }
        if aggregation_refs(request):
            report["aggregation_sources"] = [
                ref.model_dump(mode="json") for ref in aggregation_refs(request)
            ]
        async with self.factory() as session:
            _, request, _ = await self._check(session, guard, source_required=True)
            if request.candidate_version_id != version.id:
                raise LearningError("candidate_identity_invalid")
            request.validation_report, request.validation_report_hash = report, content_hash(report)
            await self._advance(session, guard, request, "review", status="ready_for_review")
            await session.commit()

    async def fail(self, guard, code):
        async with self.factory() as session:
            try:
                job, request = await guard.check(session)
            except LearningError:
                await session.execute(
                    update(MaintenanceJobRecord)
                    .where(
                        MaintenanceJobRecord.id == guard.job_id,
                        MaintenanceJobRecord.lease_owner == guard.owner,
                        MaintenanceJobRecord.lease_epoch == guard.epoch,
                        MaintenanceJobRecord.cancel_requested.is_(True),
                        MaintenanceJobRecord.lease_expires_at > await database_now(session),
                    )
                    .values(status="cancelled", lease_owner=None, lease_expires_at=None)
                    .execution_options(synchronize_session=False)
                )
                await session.commit()
                return  # an expired owner cannot change either request or job
            request.status = (
                "waiting_disabled"
                if code == "learning_discovery_not_authorized"
                else "waiting_budget"
                if code
                in {
                    "learning_waiting_budget",
                    "learning_budget_unapproved",
                    "learning_price_unknown",
                }
                else "failed"
            )
            request.error_code = code[:128]
            request.lock_version += 1
            job.status, job.error_code = "failed", code[:128]
            job.next_attempt_at = None  # only an explicit, fenced retry can spend again
            job.lease_owner = job.lease_expires_at = None
            await session.commit()

    async def exhausted(self, job_id, epoch):
        # Called after the claim transaction has released its job lock. Control
        # operations use Run -> Workspace -> Request -> Job, never the reverse.
        async with self.factory() as session:
            job = await session.get(MaintenanceJobRecord, job_id)
            if job is None or job.learning_request_id is None:
                return
            initial = await session.get(LearningRequestRecord, job.learning_request_id)
            await PersonalSourceService(self.factory)._lock_run_scope(
                session, initial.origin_run_id
            )
            request = await session.scalar(
                select(LearningRequestRecord)
                .where(LearningRequestRecord.id == initial.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            current = await session.get(MaintenanceJobRecord, job_id, populate_existing=True)
            if (
                current.lease_epoch == epoch
                and current.error_code == "maintenance_attempts_exhausted"
                and current.status == "failed"
                and request.status in {"queued", "running"}
                and request.lock_version == current.payload.get("request_lock_version")
            ):
                request.status, request.error_code = "failed", "learning_attempts_exhausted"
                request.lock_version += 1
                await session.commit()

    async def periodic_reconciliation(self):
        from sqlalchemy.exc import IntegrityError, SQLAlchemyError

        while True:
            try:
                async with self.factory() as session:
                    outstanding = await session.scalar(
                        select(LearningSpendReservationRecord.id)
                        .where(LearningSpendReservationRecord.status == "reserved")
                        .limit(1)
                    )
                    if outstanding is not None:
                        now = await database_now(session)
                        key = "learning-budget-reconcile:" + now.strftime("%Y%m%d%H%M")
                        if not await session.scalar(
                            select(MaintenanceJobRecord.id).where(
                                MaintenanceJobRecord.dedupe_key == key
                            )
                        ):
                            session.add(
                                MaintenanceJobRecord(
                                    dedupe_key=key,
                                    kind="learning_budget_reconcile",
                                    priority=50,
                                    payload={"limit": 100},
                                )
                            )
                            await session.commit()
            except (IntegrityError, SQLAlchemyError):
                pass  # another process may enqueue the same bounded minute job
            await asyncio.sleep(60)

    async def revoke_sources(self, lease):
        async with self.factory() as session:
            job = await MaintenanceLeaseGuard(lease).check(session)
            source = await session.get(LearningSourceRecord, UUID(job.payload["source_id"]))
            if (
                source is None
                or source.status == "valid"
                or source.revocation_epoch != job.payload["revocation_epoch"]
            ):
                raise LearningError("source_revocation_identity_invalid")
            artifact = await session.get(ArtifactRecord, source.artifact_id)
            uri = artifact.uri
        await self.store.erase(uri)
        async with self.factory() as session:
            await MaintenanceLeaseGuard(lease).check(session)
            artifact = await session.get(ArtifactRecord, source.artifact_id)
            artifact.attributes = {**artifact.attributes, "erased": True}
            # Revocation removes the derived source body, never rewrites historical
            # immutable Skill definitions. P4 access guards exclude invalid sources.
            await finish_in_transaction(
                session, lease, {"source_id": str(source.id), "content_erased": True}
            )
            await session.commit()
