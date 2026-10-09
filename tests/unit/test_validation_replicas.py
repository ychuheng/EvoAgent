"""Replica input ownership, identity and bounds; no user project is read."""

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


def test_registry_refuses_mutable_content_and_excessive_file_counts(tmp_path):
    with pytest.raises(LearningError, match="immutable_bytes"):
        ValidationReplicaFactory(tmp_path, (fixture((("file.txt", bytearray(b"public")),)),))
    with pytest.raises(LearningError, match="file_bound"):
        ValidationReplicaFactory(
            tmp_path, (fixture(tuple((f"file_{index}.txt", b"public") for index in range(17))),)
        )
