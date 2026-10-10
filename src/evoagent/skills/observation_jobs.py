"""Fee-free observation outbox with maintenance epoch fencing."""

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC
from uuid import UUID

from sqlalchemy import select

from evoagent.db.models import MaintenanceJobRecord
from evoagent.learning.schema import LearningError
from evoagent.skills.observation_evidence import OBSERVABLE_TERMINAL_STATES
from evoagent.tasks.lease_guard import database_now


async def schedule_observation(session, run, feedback_revision=0):
    if (
        run.data_role != "personal"
        or not run.config_snapshot
        or run.config_snapshot.get("schema_version") != 3
        or not any(
            item.get("origin") == "trial" for item in run.config_snapshot.get("selected_skills", ())
        )
        or str(run.status) not in OBSERVABLE_TERMINAL_STATES
        or run.ended_at is None
    ):
        return None
    key = f"observe:v1:{run.id}:{feedback_revision}"
    existing = await session.scalar(
        select(MaintenanceJobRecord).where(MaintenanceJobRecord.dedupe_key == key)
    )
    if existing is not None:
        return existing
    job = MaintenanceJobRecord(
        dedupe_key=key,
        kind="learning_observe",
        priority=100,
        payload={"run_id": str(run.id), "feedback_revision": feedback_revision},
    )
    session.add(job)
    return job


@dataclass(frozen=True)
class ObservationJobGuard:
    job_id: UUID
    owner: str
    epoch: int
    run_id: UUID
    revision: int

    async def check(self, session):
        job = await session.scalar(
            select(MaintenanceJobRecord)
            .where(MaintenanceJobRecord.id == self.job_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        now = await database_now(session)
        expiry = job.lease_expires_at if job else None
        if expiry is not None and expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=UTC)
        if (
            job is None
            or job.kind != "learning_observe"
            or job.status != "running"
            or job.lease_owner != self.owner
            or job.lease_epoch != self.epoch
            or job.cancel_requested
            or expiry is None
            or expiry <= now
            or job.dedupe_key != f"observe:v1:{self.run_id}:{self.revision}"
            or job.payload != {"run_id": str(self.run_id), "feedback_revision": self.revision}
        ):
            raise LearningError("observation_job_fenced")
        return job


class ObservationJobHandler:
    def __init__(self, factory):
        self.factory = factory

    async def periodic_reconciliation(self):
        from evoagent.skills.usage import SkillUsageService

        usage = SkillUsageService(self.factory)
        observations_cursor = trials_cursor = None
        while True:
            try:
                _, observations_cursor = await usage.scan_pending_observations(
                    after_id=observations_cursor, limit=50
                )
                _, trials_cursor = await usage.scan_trial_health(after_id=trials_cursor, limit=50)
            except asyncio.CancelledError:
                raise
            except Exception:
                # No secret-bearing payload, exception message or raw traceback.
                # Pending projection is still a selection-blocking condition.
                logging.getLogger(__name__).error("observation_reconciliation_failed")
            await asyncio.sleep(30)

    async def execute(self, job_id, owner, epoch):
        from evoagent.skills.usage import SkillUsageService

        async with self.factory() as session:
            job = await session.get(MaintenanceJobRecord, job_id)
            try:
                run_id = UUID(job.payload["run_id"])
                revision = job.payload["feedback_revision"]
                if type(revision) is not int or revision < 0:
                    raise ValueError
            except (AttributeError, KeyError, TypeError, ValueError):
                raise LearningError("observation_job_identity_invalid") from None
            guard = ObservationJobGuard(job_id, owner, epoch, run_id, revision)
            await guard.check(session)
            await session.commit()
        observations = await SkillUsageService(self.factory).collect(
            run_id, revision, job_guard=guard
        )
        async with self.factory() as session:
            job = await guard.check(session)
            job.status = "completed"
            job.result = {"observation_ids": [str(item) for item in observations]}
            job.lease_owner = None
            job.lease_expires_at = None
            job.error_code = None
            await session.commit()
