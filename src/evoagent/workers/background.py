"""Short fences and atomic completion for registered maintenance handlers."""

from dataclasses import dataclass
from datetime import UTC
from uuid import UUID

from sqlalchemy import select

from evoagent.db.models import MaintenanceJobRecord
from evoagent.learning.schema import LearningError
from evoagent.tasks.lease_guard import database_now


@dataclass(frozen=True, slots=True)
class BackgroundLease:
    job_id: UUID
    owner: str
    epoch: int


class MaintenanceLeaseGuard:
    def __init__(self, lease: BackgroundLease):
        self.lease = lease

    async def check(self, session):
        job = await session.scalar(
            select(MaintenanceJobRecord)
            .where(MaintenanceJobRecord.id == self.lease.job_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        now = await database_now(session)
        expiry = job.lease_expires_at if job else None
        if expiry is not None and expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=UTC)
        if (
            job is None
            or job.status != "running"
            or job.lease_owner != self.lease.owner
            or job.lease_epoch != self.lease.epoch
            or expiry is None
            or expiry <= now
            or job.cancel_requested
        ):
            raise LearningError("maintenance_job_fenced")
        return job


async def finish_in_transaction(session, lease: BackgroundLease, result):
    job = await MaintenanceLeaseGuard(lease).check(session)
    job.status = "completed"
    job.result = result
    job.error_code = None
    job.lease_owner = None
    job.lease_expires_at = None
    # The caller owns the transaction containing stage results and this finish.
