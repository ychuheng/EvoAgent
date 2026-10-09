# Frozen pre-P6a reference: 7610cd596b085e0dadf791bf582db7fa8266d156
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import or_, select, update

from evoagent.db.models import MaintenanceJobRecord
from evoagent.tasks.lease_guard import database_now


class MaintenanceWorker:
    def __init__(self, factory, artifact_store, index_service=None):
        self.factory = factory
        self.store = artifact_store
        self.owner = f"maintenance:{uuid4().hex}"
        self.index_service = index_service

    async def claim(self):
        async with self.factory() as session:
            now = await database_now(session)
            job = await session.scalar(
                select(MaintenanceJobRecord)
                .where(
                    or_(
                        MaintenanceJobRecord.status == "pending",
                        (MaintenanceJobRecord.status == "failed")
                        & (MaintenanceJobRecord.attempts < 3)
                        & (MaintenanceJobRecord.next_attempt_at <= now),
                        (MaintenanceJobRecord.status == "running")
                        & (MaintenanceJobRecord.lease_expires_at <= now),
                    )
                )
                .where(
                    MaintenanceJobRecord.kind.in_(
                        ("archive", "erase", "index_source", "index_rebuild")
                        if self.index_service
                        else ("archive", "erase")
                    )
                )
                .order_by(MaintenanceJobRecord.created_at)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if job is None:
                return None
            if job.attempts >= 3:
                job.status = "failed"
                job.error_code = "maintenance_attempts_exhausted"
                job.next_attempt_at = None
                job.lease_owner = None
                job.lease_expires_at = None
                await session.commit()
                return None
            epoch = job.lease_epoch
            changed = await session.execute(
                update(MaintenanceJobRecord)
                .where(
                    MaintenanceJobRecord.id == job.id,
                    MaintenanceJobRecord.lease_epoch == epoch,
                )
                .values(
                    status="running",
                    lease_owner=self.owner,
                    lease_epoch=epoch + 1,
                    lease_expires_at=now + timedelta(seconds=120),
                    attempts=job.attempts + 1,
                )
            )
            if changed.rowcount != 1:
                return None
            await session.commit()
            return job.id, epoch + 1
