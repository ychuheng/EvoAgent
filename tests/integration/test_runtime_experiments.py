import pytest
from sqlalchemy import select

from evoagent.config import Settings
from evoagent.db.base import Base
from evoagent.db.models import (
    EvalRunRecord,
    MessageRecord,
    SessionRecord,
)
from evoagent.db.session import Database
from evoagent.evals.datasets import EvalDatasetService
from evoagent.evals.runtime import RuntimeExperimentService
from evoagent.evals.runtime_schema import RuntimeArm, RuntimeExperimentSpec
from evoagent.evals.schema import EvalCaseDefinition, EvalDatasetDefinition, ValidatorSpec
from evoagent.memory.archival import enqueue_archive
from evoagent.memory.repository import source_message
from evoagent.memory.schema import MemoryError
from evoagent.tasks.lease import JobLeaseManager
from evoagent.workers.bootstrap import ConfiguredTaskHandler
from evoagent.workers.main import JobWorker


@pytest.fixture
async def database(tmp_path):
    db = Database(f"sqlite+aiosqlite:///{tmp_path / 'runtime.db'}")
    async with db.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield db
    await db.dispose()


async def test_runtime_end_to_end_isolated_and_failed_samples_retained(database, tmp_path):
    datasets = EvalDatasetService(database.session_factory)
    dataset = await datasets.import_definition(
        EvalDatasetDefinition(
            name="runtime-fixture",
            version=1,
            cases=(
                EvalCaseDefinition(
                    case_key="failure-retained",
                    task_family="long_history",
                    split="holdout",
                    public_input={
                        "goal": "write report",
                        "history": [{"role": "user", "content": "earlier public constraint"}],
                    },
                    private_validators=(
                        ValidatorSpec(
                            name="preserves_constraints",
                            parameters={"required": ["PRIVATE_SENTINEL_NEVER_SEND"]},
                        ),
                    ),
                ),
            ),
        )
    )
    dataset = await datasets.freeze(dataset.id)
    service = RuntimeExperimentService(database.session_factory)
    experiment = await service.create(
        dataset.id,
        RuntimeExperimentSpec(
            experiment_variable="context_policy",
            control=RuntimeArm(),
            treatment=RuntimeArm(context_policy="legacy"),
            repeats=1,
            model="mock-model",
            code_version="test",
            dataset_hash=dataset.content_hash,
            environment={"test": "SQLite mocked provider"},
        ),
    )
    settings = Settings(
        _env_file=None, artifact_root=tmp_path / "artifacts", workspace=tmp_path, model="mock-model"
    )
    worker = JobWorker(
        worker_id="runtime-test",
        lease_manager=JobLeaseManager(
            database.session_factory,
            lease_seconds=30,
            runtime_experiment_id=experiment.id,
            runtime_arm="control",
        ),
        handler=ConfiguredTaskHandler(settings, database),
        heartbeat_seconds=0.1,
        poll_seconds=0.1,
        snapshot_schema_version=2,
    )
    assert await service.collect(experiment.id) is None
    for arm in ("control", "treatment"):
        worker._lease_manager._runtime_arm = arm
        await service.release(experiment.id, arm)
        assert await worker.run_once()
    report = await service.collect(experiment.id)
    assert report["report_kind"] == "mock"
    assert len(report["samples"]) == 2
    assert report["summary"]["control"]["success_rate"] == 0
    assert report == await service.collect(experiment.id)
    assert all(row["usage"]["exact_model_tokens"] is None for row in report["samples"])
    async with database.session_factory() as db:
        assert await db.scalar(select(EvalRunRecord.id)) is None
        sessions = tuple(await db.scalars(select(SessionRecord)))
        assert len({session.workspace_id for session in sessions}) == 2
        messages = tuple(await db.scalars(select(MessageRecord)))
        assert all("PRIVATE_SENTINEL" not in message.content for message in messages)
        goal = next(message for message in messages if message.kind == "goal")
        with pytest.raises(MemoryError, match="evaluation_source_forbidden"):
            await source_message(db, goal.id, goal.session_id)
    with pytest.raises(MemoryError, match="evaluation_source_forbidden"):
        await enqueue_archive(database.session_factory, goal.session_id)
    with pytest.raises(ValueError, match="finalized"):
        await service.release(experiment.id, "control")


async def test_rate_limit_checkpoint_resumes_without_network_attempt(database, tmp_path):
    from datetime import UTC, datetime, timedelta

    from redis.exceptions import ConnectionError

    from evoagent.db.models import RunRecord, RunSnapshotRecord, TaskRecord
    from evoagent.tasks.service import TaskService
    from evoagent.tasks.state_machine import TaskStatus
    from evoagent.workers.rate_limit import ServiceGate

    class Broken:
        async def eval(self, *args):
            raise ConnectionError("offline")

    settings = Settings(_env_file=None, artifact_root=tmp_path / "artifacts", workspace=tmp_path)
    service = TaskService(database.session_factory)
    scope = await service.create_session("quota")
    aggregate = await service.create_task(
        session_id=scope.id, goal="report", provider="mock", model="mock-model"
    )
    gate = ServiceGate(settings, Broken())
    worker = JobWorker(
        worker_id="quota",
        lease_manager=JobLeaseManager(database.session_factory, lease_seconds=30),
        handler=ConfiguredTaskHandler(settings, database, gate),
        heartbeat_seconds=0.1,
        poll_seconds=0.1,
        snapshot_schema_version=2,
    )
    assert await worker.run_once()
    async with database.session_factory() as db:
        task = await db.get(TaskRecord, aggregate.task.id)
        assert task.status == TaskStatus.RETRYING
        assert task.attempt_count == 1
        snapshot = await db.scalar(
            select(RunSnapshotRecord).where(RunSnapshotRecord.run_id == aggregate.run.id)
        )
        assert snapshot.state["completed_iterations"] == 0
        task.next_attempt_at = datetime.now(UTC) - timedelta(seconds=1)
        await db.commit()
    gate.client = None
    assert await worker.run_once()
    async with database.session_factory() as db:
        task = await db.get(TaskRecord, aggregate.task.id)
        run = await db.get(RunRecord, aggregate.run.id)
        assert task.status == TaskStatus.COMPLETED, run.error_message
        assert task.attempt_count == 1


async def test_eval_epoch_rejects_same_owner_after_takeover(database):
    from datetime import UTC, datetime, timedelta

    from evoagent.db.models import EvalDatasetRecord, EvalExperimentRecord
    from evoagent.evals.coordinator import EvalCoordinator, EvalLeaseLostError
    from evoagent.evals.validators import default_validator_registry

    async with database.session_factory() as db:
        dataset = EvalDatasetRecord(name="epoch", version=1, content_hash="sha256:" + "a" * 64)
        db.add(dataset)
        await db.flush()
        db.add(
            EvalExperimentRecord(
                kind="skill_comparison", dataset_id=dataset.id, config_hash="sha256:" + "b" * 64
            )
        )
        await db.commit()
    coordinator = EvalCoordinator(
        database.session_factory, default_validator_registry(), lease_seconds=1
    )
    now = datetime.now(UTC)
    first = await coordinator.claim_next("same", now=now)
    second = await coordinator.claim_next("same", now=now + timedelta(seconds=2))
    assert second.epoch == first.epoch + 1
    with pytest.raises(EvalLeaseLostError):
        await coordinator.heartbeat(first, now=now + timedelta(seconds=2.1))
    with pytest.raises(EvalLeaseLostError):
        await coordinator.run_once(first, now=now + timedelta(seconds=2.1))


async def test_queue_publish_happens_only_after_commit(database):
    from evoagent.db.models import TaskRecord
    from evoagent.tasks.service import TaskService

    class Publisher:
        calls = 0

        async def publish(self):
            async with database.session_factory() as db:
                assert await db.scalar(select(TaskRecord.id)) is not None
            self.calls += 1

    publisher = Publisher()
    database.session_factory.configure(info={"wakeup": publisher})
    service = TaskService(database.session_factory)
    scope = await service.create_session("post-commit")
    await service.create_task(session_id=scope.id, goal="test", provider="mock", model="mock")
    assert publisher.calls == 1
    async with database.session_factory() as db:
        db.add(TaskRecord(session_id=scope.id, goal="rollback", status="queued"))
        await db.flush()
        await db.rollback()
        await db.commit()
    assert publisher.calls == 1


async def test_maintenance_retry_is_due_bounded_and_fenced(database, tmp_path):
    from datetime import UTC, datetime, timedelta

    from evoagent.db.models import MaintenanceJobRecord
    from evoagent.memory.maintenance import MaintenanceWorker
    from evoagent.trace.artifacts import LocalArtifactStore

    first = MaintenanceWorker(database.session_factory, LocalArtifactStore(tmp_path))
    second = MaintenanceWorker(database.session_factory, LocalArtifactStore(tmp_path))
    async with database.session_factory() as db:
        job = MaintenanceJobRecord(
            kind="archive",
            dedupe_key="retry-test",
            payload={},
            status="failed",
            attempts=1,
            next_attempt_at=datetime.now(UTC) + timedelta(minutes=1),
        )
        db.add(job)
        await db.commit()
    assert await first.claim() is None
    async with database.session_factory() as db:
        stored = await db.get(MaintenanceJobRecord, job.id)
        stored.next_attempt_at = datetime.now(UTC) - timedelta(seconds=1)
        await db.commit()
    old = await first.claim()
    assert await second.claim() is None
    async with database.session_factory() as db:
        stored = await db.get(MaintenanceJobRecord, job.id)
        stored.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await db.commit()
    new = await second.claim()
    assert new[1] == old[1] + 1
    with pytest.raises(MemoryError, match="maintenance_lease_lost"):
        await first.execute(*old)
    async with database.session_factory() as db:
        stored = await db.get(MaintenanceJobRecord, job.id)
        stored.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await db.commit()
    assert await first.claim() is None
    async with database.session_factory() as db:
        stored = await db.get(MaintenanceJobRecord, job.id)
        assert stored.error_code == "maintenance_attempts_exhausted"
