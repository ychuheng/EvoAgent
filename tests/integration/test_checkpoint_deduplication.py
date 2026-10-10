from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import select

from evoagent.core.models import LoopState, Message, MessageRole, TokenUsage
from evoagent.db.models import RunSnapshotRecord, TaskRecord
from evoagent.db.unit_of_work import UnitOfWork
from evoagent.runtime.checkpoints import PersistentCheckpointStore
from evoagent.tasks.lease import JobLeaseManager
from evoagent.tasks.lease_guard import LeaseGuard, LeaseLostError
from tests.integration.test_checkpoints_and_artifacts import sample_state

pytest_plugins = ("tests.integration.test_checkpoints_and_artifacts",)


VARIANTS = {
    "schema_version": 2,
    "context_revision_id": uuid4(),
    "history_before_sequence": 1,
    "messages": (Message(role=MessageRole.USER, content="changed source reference"),),
    "completed_iterations": 2,
    "usage": TokenUsage(input_tokens=3, output_tokens=1, total_tokens=4),
    "known_usage": TokenUsage(input_tokens=2, output_tokens=1, total_tokens=3),
    "usage_is_complete": False,
    "previous_tool_fingerprint": "fingerprint",
    "repeated_tool_calls": 1,
    "config_hash": "b" * 64,
}


async def snapshots(database, run_id):
    async with database.session_factory() as session:
        return list(
            await session.scalars(
                select(RunSnapshotRecord)
                .where(RunSnapshotRecord.run_id == run_id)
                .order_by(RunSnapshotRecord.event_sequence)
            )
        )


def test_each_recoverable_field_has_a_valid_mutation():
    assert set(VARIANTS) == set(LoopState.model_fields)


@pytest.mark.parametrize("field", tuple(VARIANTS))
async def test_each_field_change_writes_a_new_recovery_point(persistence, field):
    database, run_id = persistence
    store = PersistentCheckpointStore(run_id, database.session_factory)
    state = sample_state().model_copy(update={"previous_tool_fingerprint": "initial"})
    await store.save_if_changed(state)
    await store.save_if_changed(state)
    assert len(await snapshots(database, run_id)) == 1
    changed = LoopState.model_validate({**state.model_dump(mode="json"), field: VARIANTS[field]})
    await store.save_if_changed(changed)
    records = await snapshots(database, run_id)
    assert len(records) == 2
    assert records[-1].state == changed.model_dump(mode="json")


async def test_off_switch_preserves_one_snapshot_per_call_and_restart_cache_is_empty(persistence):
    database, run_id = persistence
    store = PersistentCheckpointStore(
        run_id,
        database.session_factory,
        settings=SimpleNamespace(runtime_snapshot_deduplication_enabled=False),
    )
    state = sample_state()
    for _ in range(3):
        await store.save(state)
    records = await snapshots(database, run_id)
    assert len(records) == 3
    assert [record.state for record in records] == [state.model_dump(mode="json")] * 3
    fresh = PersistentCheckpointStore(run_id, database.session_factory)
    await fresh.save_if_changed(state)
    assert len(await snapshots(database, run_id)) == 4


async def test_on_switch_skips_only_duplicate_writes(persistence):
    database, run_id = persistence
    store = PersistentCheckpointStore(
        run_id,
        database.session_factory,
        settings=SimpleNamespace(runtime_snapshot_deduplication_enabled=True),
    )
    await store.save(sample_state())
    await store.save(sample_state())
    assert len(await snapshots(database, run_id)) == 1


async def test_unchanged_state_still_checks_authority(persistence):
    database, run_id = persistence

    class Guard:
        lease = SimpleNamespace(run_id=run_id)
        denied = False
        calls = 0

        async def check(self, session):
            self.calls += 1
            if self.denied:
                raise ValueError("fenced")

    guard = Guard()
    store = PersistentCheckpointStore(run_id, database.session_factory, lease_guard=guard)
    await store.save_if_changed(sample_state())
    guard.denied = True
    with pytest.raises(ValueError, match="fenced"):
        await store.save_if_changed(sample_state())
    assert guard.calls == 2
    assert store._committed_digest is None
    assert len(await snapshots(database, run_id)) == 1


async def test_ambiguous_commit_invalidates_digest(persistence, monkeypatch):
    database, run_id = persistence
    store = PersistentCheckpointStore(run_id, database.session_factory)
    await store.save_if_changed(sample_state())
    changed = sample_state().model_copy(update={"completed_iterations": 2})
    original = UnitOfWork.commit

    async def uncertain(unit):
        await original(unit)
        raise RuntimeError("commit response lost")

    with monkeypatch.context() as patch:
        patch.setattr(UnitOfWork, "commit", uncertain)
        with pytest.raises(RuntimeError, match="commit response lost"):
            await store.save_if_changed(changed)
    assert store._committed_digest is None
    await store.save_if_changed(changed)
    assert len(await snapshots(database, run_id)) == 3


async def test_real_lease_epoch_fences_duplicate_snapshot(persistence):
    database, run_id = persistence
    lease = await JobLeaseManager(database.session_factory, lease_seconds=30).claim_next("o9-test")
    assert lease.run_id == run_id
    store = PersistentCheckpointStore(
        run_id, database.session_factory, lease_guard=LeaseGuard(lease)
    )
    await store.save_if_changed(sample_state())
    async with database.session_factory() as session:
        task = await session.get(TaskRecord, lease.task_id)
        task.lease_epoch += 1
        await session.commit()
    with pytest.raises(LeaseLostError):
        await store.save_if_changed(sample_state())
    assert len(await snapshots(database, run_id)) == 1
