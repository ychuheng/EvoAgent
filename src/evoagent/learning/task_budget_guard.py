"""Task leases are not maintenance-job identities."""

from evoagent.db.models import LearningRequestRecord
from evoagent.learning.schema import LearningError
from evoagent.tasks.lease_guard import LeaseGuard


class ValidationTaskBudgetGuard:
    def __init__(self, lease, validation_guard):
        if lease.run_id != validation_guard.run_id:
            raise LearningError("validation_budget_identity_invalid")
        self.lease, self.validation_guard = lease, validation_guard

    def reservation_binding(self):
        return dict(
            dispatcher_kind="task",
            job_id=None,
            job_epoch=None,
            task_id=self.lease.task_id,
            run_id=self.lease.run_id,
            task_epoch=self.lease.epoch,
        )

    async def lock_lease(self, session):
        task, _ = await LeaseGuard(self.lease).check(session)
        if task.cancel_requested:
            raise LearningError("validation_task_cancelled")

    async def check(self, session):
        await self.lock_lease(session)
        await self.validation_guard._verify(session)
        request = await session.get(LearningRequestRecord, self.validation_guard.request_id)
        return self.lease, request
