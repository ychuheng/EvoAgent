"""Learning jobs retain the existing maintenance lease and request fences."""

from dataclasses import dataclass
from datetime import UTC
from uuid import UUID

from sqlalchemy import select

from evoagent.db.models import LearningRequestRecord, MaintenanceJobRecord
from evoagent.learning.schema import LearningError
from evoagent.tasks.lease_guard import database_now


@dataclass(frozen=True, slots=True)
class LearningJobGuard:
    job_id: UUID
    owner: str
    epoch: int

    async def check(self, session):
        found = await session.get(MaintenanceJobRecord, self.job_id)
        if found is None or found.learning_request_id is None:
            raise LearningError("learning_job_fenced")
        # Control-plane cancellation locks request before updating its jobs.
        request = await session.scalar(
            select(LearningRequestRecord)
            .where(LearningRequestRecord.id == found.learning_request_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        job = await session.scalar(
            select(MaintenanceJobRecord)
            .where(MaintenanceJobRecord.id == self.job_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        now = await database_now(session)
        expiry = job.lease_expires_at
        if expiry is not None and expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=UTC)
        if (
            job.lease_owner != self.owner
            or job.lease_epoch != self.epoch
            or job.status != "running"
            or job.cancel_requested
            or expiry is None
            or expiry <= now
            or request is None
            or request.status not in {"queued", "running"}
            or job.payload.get("request_lock_version") != request.lock_version
        ):
            raise LearningError("learning_job_fenced")
        return job, request
