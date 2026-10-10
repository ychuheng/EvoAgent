"""Initial replica bindings are immutable; task edits are not initial inputs."""

from sqlalchemy import select

from evoagent.db.models import ValidationReplicaBindingRecord
from evoagent.learning.replicas import ValidationReplicaFactory, default_personal_fixtures
from evoagent.learning.schema import LearningError
from evoagent.skills.canonical import content_hash
from evoagent.trace.artifacts import LocalArtifactStore


def replica_factory(store, *, fixtures=()):
    if not isinstance(store, LocalArtifactStore):
        raise LearningError("validation_replica_local_storage_required")
    return ValidationReplicaFactory(store.root / "personal-validation-replicas", fixtures)


async def prepare_replicas(store, request, cases):
    manifests = request.frozen_inputs.get("fixture_manifests", {})
    if not manifests:
        return {}
    if content_hash(manifests) != request.frozen_inputs.get("fixture_manifest_hash"):
        raise LearningError("validation_fixture_manifest_invalid")
    factory = replica_factory(store, fixtures=default_personal_fixtures())
    prepared = {}
    for case in cases:
        if case.fixture_id is None:
            continue
        fixture = factory.fixtures.get(case.fixture_id)
        if fixture is None or fixture.manifest() != manifests.get(case.case_key):
            raise LearningError("validation_fixture_registry_changed")
        for repeat in range(request.policy_snapshot["repeats"]):
            for arm in ("control", "treatment"):
                prepared[case.case_key, arm, repeat] = await factory.create(
                    request.id, case.case_key, arm, repeat, case.fixture_id
                )
    return prepared


async def require_replica_binding(session, request, evaluation, case):
    binding = await session.scalar(
        select(ValidationReplicaBindingRecord).where(
            ValidationReplicaBindingRecord.run_id == evaluation.run_id
        )
    )
    if case.fixture_id is None:
        if binding is not None:
            raise LearningError("validation_replica_binding_invalid")
        return None
    manifests = request.frozen_inputs.get("fixture_manifests", {})
    initial = manifests.get(case.case_key)
    if (
        initial is None
        or content_hash(manifests) != request.frozen_inputs.get("fixture_manifest_hash")
        or request.policy_snapshot.get("fixture_manifest_hash") != content_hash(manifests)
    ):
        raise LearningError("validation_replica_binding_invalid")
    expected = {
        "request_id": str(request.id),
        "case_key": case.case_key,
        "arm": evaluation.arm,
        "repeat": evaluation.repeat_index,
        "fixture_hash": content_hash(initial),
        **initial,
    }
    if (
        binding is None
        or binding.workspace_id != request.workspace_id
        or binding.request_id != request.id
        or binding.eval_run_id != evaluation.id
        or binding.case_key != case.case_key
        or binding.arm != evaluation.arm
        or binding.repeat_index != evaluation.repeat_index
        or binding.fixture_id != case.fixture_id
        or binding.input_fingerprint != request.frozen_inputs["input_fingerprints"][case.case_key]
        or binding.manifest != expected
        or binding.manifest_hash != content_hash(expected)
    ):
        raise LearningError("validation_replica_binding_invalid")
    return binding
