"""Replica input ownership, identity and bounds; no user project is read."""

import os
from uuid import uuid4

import pytest

from evoagent.learning.replicas import RegisteredFixture, ValidationReplicaFactory
from evoagent.learning.schema import LearningError


def fixture(files=(("inputs/rows.csv", b"id\n001\n"),)):
    return RegisteredFixture("rows_positive", files)


async def test_arms_have_equal_inputs_but_distinct_directories_and_idempotent_identity(tmp_path):
    factory = ValidationReplicaFactory(tmp_path, (fixture(),))
    request = uuid4()
    control = await factory.create(request, "case_one", "control", 0, "rows_positive")
    treatment = await factory.create(request, "case_one", "treatment", 0, "rows_positive")
    assert control.root != treatment.root
    assert control.fixture_hash == treatment.fixture_hash
    assert control.input_manifest == treatment.input_manifest
    assert (await factory.create(request, "case_one", "control", 0, "rows_positive")) == control
    (treatment.root / "inputs/rows.csv").write_bytes(b"changed")
    with pytest.raises(LearningError, match="input_changed"):
        await factory.create(request, "case_one", "treatment", 0, "rows_positive")
    assert (control.root / "inputs/rows.csv").read_bytes() == b"id\n001\n"


@pytest.mark.parametrize(
    "name", ["../secret", "/secret", "C:/secret", "dir\\file", ".git/config", "CON.txt", "dir/row."]
)
def test_fixture_paths_cannot_read_host_paths_or_alias_windows_paths(tmp_path, name):
    with pytest.raises(LearningError, match="path_invalid"):
        ValidationReplicaFactory(tmp_path, (fixture(((name, b"public"),)),))


@pytest.mark.parametrize(
    "files", [(("same.txt", b"a"), ("SAME.txt", b"b")), (("dir/file.txt", b"a"), ("DIR", b"b"))]
)
def test_cross_platform_file_aliases_are_refused(tmp_path, files):
    with pytest.raises(LearningError, match="collision"):
        ValidationReplicaFactory(tmp_path, (fixture(files),))


@pytest.mark.parametrize(
    "content",
    [b"x" * (256 * 1024 + 1), b"\x00binary", b'password = "fixture-secret"'],
    ids=["oversize", "binary", "sensitive"],
)
def test_fixture_bytes_and_sensitive_content_are_bounded(tmp_path, content):
    with pytest.raises(LearningError):
        ValidationReplicaFactory(tmp_path, (fixture((("input.txt", content),)),))


async def test_unregistered_fixture_has_no_filesystem_side_effect(tmp_path):
    factory = ValidationReplicaFactory(tmp_path / "replicas", ())
    with pytest.raises(LearningError, match="not_registered"):
        await factory.create(uuid4(), "case_one", "control", 0, "unknown_fixture")
    assert not factory.root.exists()


async def test_missing_or_modified_manifest_does_not_recreate_inputs(tmp_path):
    factory = ValidationReplicaFactory(tmp_path, (fixture(),))
    request = uuid4()
    replica = await factory.create(request, "case_one", "control", 0, "rows_positive")
    (replica.root / ".validation-input-manifest.json").unlink()
    with pytest.raises(LearningError, match="manifest_changed"):
        await factory.create(request, "case_one", "control", 0, "rows_positive")


def test_fixture_data_identity_ignores_registry_ids_and_file_renames():
    first = RegisteredFixture("first_fixture", (("old.csv", b"id\n001\n"),))
    renamed = RegisteredFixture("second_fixture", (("new.csv", b"id\n001\n"),))
    changed = RegisteredFixture("third_fixture", (("old.csv", b"id\n002\n"),))
    assert first.input_fingerprint() == renamed.input_fingerprint()
    assert first.input_fingerprint() != changed.input_fingerprint()
    duplicated = RegisteredFixture(
        "duplicate_fixture", (("a.csv", b"id\n001\n"), ("b.csv", b"id\n001\n"))
    )
    assert duplicated.input_fingerprint() == first.input_fingerprint()


async def test_bound_resume_preserves_outputs_and_does_not_depend_on_current_registry(tmp_path):
    factory = ValidationReplicaFactory(tmp_path, (fixture(),))
    request = uuid4()
    replica = await factory.create(request, "case_one", "treatment", 0, "rows_positive")
    # This stands in for the descriptor frozen by a trusted execution binding.
    descriptor = replica.binding_manifest
    (replica.root / "inputs/rows.csv").write_bytes(b"legitimate edit")
    (replica.root / "result.txt").write_bytes(b"completed output")
    resumed = await ValidationReplicaFactory(tmp_path, ()).resume(
        request, "case_one", "treatment", 0, expected_manifest=descriptor
    )
    assert resumed == replica
    assert (resumed.root / "inputs/rows.csv").read_bytes() == b"legitimate edit"
    assert (resumed.root / "result.txt").read_bytes() == b"completed output"


async def test_resume_rejects_cross_arm_binding_or_changed_manifest_without_rewriting(tmp_path):
    factory = ValidationReplicaFactory(tmp_path, (fixture(),))
    request = uuid4()
    control = await factory.create(request, "case_one", "control", 0, "rows_positive")
    treatment = await factory.create(request, "case_one", "treatment", 0, "rows_positive")
    descriptor = control.binding_manifest
    with pytest.raises(LearningError, match="manifest_changed"):
        await factory.resume(request, "case_one", "treatment", 0, expected_manifest=descriptor)
    (control.root / ".validation-input-manifest.json").write_bytes(b"broken")
    with pytest.raises(LearningError, match="manifest_changed"):
        await factory.resume(request, "case_one", "control", 0, expected_manifest=descriptor)
    assert (control.root / ".validation-input-manifest.json").read_bytes() == b"broken"
    assert (treatment.root / "inputs/rows.csv").read_bytes() == b"id\n001\n"


async def test_resume_never_creates_a_missing_bound_replica(tmp_path):
    factory = ValidationReplicaFactory(tmp_path / "absent", ())
    with pytest.raises(LearningError, match="path_unsafe"):
        await factory.resume(uuid4(), "case_one", "control", 0, expected_manifest={})
    assert not factory.root.exists()


@pytest.mark.skipif(os.name == "nt", reason="real symlink creation needs Windows privileges")
async def test_resume_refuses_replaced_replica_symlink_and_registry_root_alias(tmp_path):
    factory = ValidationReplicaFactory(tmp_path / "replicas", (fixture(),))
    request = uuid4()
    replica = await factory.create(request, "case_one", "control", 0, "rows_positive")
    moved = tmp_path / "moved"
    replica.root.rename(moved)
    replica.root.symlink_to(moved, target_is_directory=True)
    with pytest.raises(LearningError, match="path_unsafe"):
        await factory.resume(
            request, "case_one", "control", 0, expected_manifest=replica.binding_manifest
        )
    alias = tmp_path / "alias"
    alias.symlink_to(factory.root, target_is_directory=True)
    with pytest.raises(LearningError, match="root_unsafe"):
        ValidationReplicaFactory(alias / "nested", (fixture(),))


def test_registry_refuses_mutable_content_and_excessive_file_counts(tmp_path):
    with pytest.raises(LearningError, match="immutable_bytes"):
        ValidationReplicaFactory(tmp_path, (fixture((("file.txt", bytearray(b"public")),)),))
    with pytest.raises(LearningError, match="file_bound"):
        ValidationReplicaFactory(
            tmp_path, (fixture(tuple((f"file_{index}.txt", b"public") for index in range(17))),)
        )
