"""Bounded, host-registered fixtures; never copy a user's project implicitly."""

import asyncio
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from uuid import UUID

from evoagent.learning.schema import LearningError
from evoagent.privacy.redaction import detect_sensitive
from evoagent.skills.canonical import canonical_json, content_hash

MAX_FIXTURE_BYTES = 256 * 1024
MAX_FIXTURE_FILES = 16
_RESERVED = {"con", "prn", "aux", "nul"} | {
    f"{prefix}{index}" for prefix in ("com", "lpt") for index in range(1, 10)
}


def _is_link(path):
    return path.is_symlink() or path.is_junction()


@dataclass(frozen=True, slots=True)
class RegisteredFixture:
    """Only host code may populate this registry; API payloads contain an ID."""

    fixture_id: str
    files: tuple[tuple[str, bytes], ...]

    def input_fingerprint(self):
        """Conservative data identity: neither registry nor file renames add evidence."""
        manifest = self.manifest()
        return content_hash(
            [
                {"sha256": digest, "size_bytes": size}
                for digest, size in sorted(
                    {(row["sha256"], row["size_bytes"]) for row in manifest["files"]}
                )
            ]
        )

    def manifest(self):
        if type(self.files) is not tuple or any(
            type(item) is not tuple
            or len(item) != 2
            or type(item[0]) is not str
            or type(item[1]) is not bytes
            for item in self.files
        ):
            raise LearningError("validation_fixture_requires_immutable_bytes")
        if type(self.fixture_id) is not str or not re.fullmatch(
            r"[a-z][a-z0-9_-]{1,63}", self.fixture_id
        ):
            raise LearningError("validation_fixture_id_invalid")
        if not 1 <= len(self.files) <= MAX_FIXTURE_FILES:
            raise LearningError("validation_fixture_file_bound")
        names, rows, total = set(), [], 0
        for name, data in self.files:
            path = PurePosixPath(name)
            if (
                not name
                or "\\" in name
                or ":" in name
                or path.is_absolute()
                or any(part in {".", "..", ""} for part in name.split("/"))
                or len(name) > 256
                or len(path.parts) > 4
                or any(part.startswith(".") for part in path.parts)
                or any(part.endswith((".", " ")) for part in path.parts)
                or any(part.split(".", 1)[0].casefold() in _RESERVED for part in path.parts)
                or name in names
            ):
                raise LearningError("validation_fixture_path_invalid")
            names.add(name)
            total += len(data)
            if total > MAX_FIXTURE_BYTES:
                raise LearningError("validation_fixture_byte_bound")
            try:
                text = data.decode("utf8")
            except UnicodeDecodeError as error:
                raise LearningError("validation_fixture_requires_utf8") from error
            if "\0" in text or detect_sensitive(text):
                raise LearningError("validation_fixture_sensitive_or_binary")
            rows.append(
                {"path": name, "sha256": hashlib.sha256(data).hexdigest(), "size_bytes": len(data)}
            )
        # Case-insensitive filesystems must not merge independent inputs.
        if len({name.casefold() for name in names}) != len(names):
            raise LearningError("validation_fixture_path_collision")
        for name in names:
            if any(
                parent.as_posix().casefold() in {item.casefold() for item in names}
                for parent in PurePosixPath(name).parents
            ):
                raise LearningError("validation_fixture_path_collision")
        return {"fixture_id": self.fixture_id, "files": sorted(rows, key=lambda row: row["path"])}


@dataclass(frozen=True, slots=True)
class ValidationReplica:
    root: Path
    input_manifest: dict
    fixture_hash: str
    binding_manifest: dict


class ValidationReplicaFactory:
    def __init__(self, root: Path, fixtures: tuple[RegisteredFixture, ...]):
        if any(_is_link(path) for path in (root.absolute(), *root.absolute().parents)):
            raise LearningError("validation_replica_root_unsafe")
        self.root = root.resolve()
        self.fixtures = {item.fixture_id: item for item in fixtures}
        if len(self.fixtures) != len(fixtures):
            raise LearningError("validation_fixture_registry_duplicate")
        for fixture in fixtures:
            fixture.manifest()

    async def create(self, request_id: UUID, case_key: str, arm: str, repeat: int, fixture_id: str):
        if (
            not isinstance(request_id, UUID)
            or not re.fullmatch(r"[a-z][a-z0-9_-]{1,63}", case_key)
            or arm not in {"control", "treatment"}
            or type(repeat) is not int
            or not 0 <= repeat < 3
        ):
            raise LearningError("validation_replica_identity_invalid")
        fixture = self.fixtures.get(fixture_id)
        if fixture is None:
            raise LearningError("validation_fixture_not_registered")
        return await asyncio.to_thread(self._create, request_id, case_key, arm, repeat, fixture)

    async def resume(
        self, request_id: UUID, case_key: str, arm: str, repeat: int, *, expected_manifest: dict
    ):
        """Restore a durable host binding without resetting legitimately edited files.

        expected_manifest must come from the immutable execution binding, never
        from model/user arguments or the mutable replica itself. Initial byte
        verification belongs to create; later project authorization guards own
        each file operation. Registry changes cannot rewrite an existing run.
        """
        if (
            not isinstance(request_id, UUID)
            or not re.fullmatch(r"[a-z][a-z0-9_-]{1,63}", case_key)
            or arm not in {"control", "treatment"}
            or type(repeat) is not int
            or not 0 <= repeat < 3
        ):
            raise LearningError("validation_replica_identity_invalid")
        return await asyncio.to_thread(
            self._resume, request_id, case_key, arm, repeat, expected_manifest
        )

    def _resume(self, request_id, case_key, arm, repeat, expected_manifest):
        target = self.root / f"{request_id.hex}-{case_key}-{repeat}-{arm}"
        saved = target / ".validation-input-manifest.json"
        if (
            _is_link(self.root)
            or self.root.resolve() != self.root
            or _is_link(target)
            or not target.is_dir()
            or _is_link(saved)
            or target.resolve().parent != self.root
        ):
            raise LearningError("validation_replica_path_unsafe")
        try:
            expected = canonical_json(expected_manifest).encode("utf8")
            with saved.open("rb") as handle:
                raw = handle.read(16385)
            if len(expected) > 16384 or len(raw) > 16384 or raw != expected:
                raise LearningError("validation_replica_manifest_changed")
            manifest = json.loads(raw)
            if (
                manifest["request_id"] != str(request_id)
                or manifest["case_key"] != case_key
                or manifest["arm"] != arm
                or manifest["repeat"] != repeat
                or content_hash({"fixture_id": manifest["fixture_id"], "files": manifest["files"]})
                != manifest["fixture_hash"]
            ):
                raise LearningError("validation_replica_manifest_changed")
        except (OSError, ValueError, KeyError, TypeError):
            raise LearningError("validation_replica_manifest_changed") from None
        return ValidationReplica(
            target,
            {"fixture_id": manifest["fixture_id"], "files": manifest["files"]},
            manifest["fixture_hash"],
            manifest,
        )

    def _create(self, request_id, case_key, arm, repeat, fixture):
        manifest = fixture.manifest()
        digest = content_hash(manifest)
        # A flat host-owned directory avoids sharing a case directory between
        # arms or accepting any user-supplied absolute/source path.
        name = f"{request_id.hex}-{case_key}-{repeat}-{arm}"
        self.root.mkdir(parents=True, exist_ok=True)
        if _is_link(self.root) or self.root.resolve() != self.root:
            raise LearningError("validation_replica_root_unsafe")
        target = self.root / name
        expected = {
            "request_id": str(request_id),
            "case_key": case_key,
            "arm": arm,
            "repeat": repeat,
            "fixture_hash": digest,
            **manifest,
        }
        saved = target / ".validation-input-manifest.json"
        try:
            target.mkdir(mode=0o700)
        except FileExistsError:
            if _is_link(target) or not target.is_dir() or _is_link(saved):
                raise LearningError("validation_replica_path_unsafe") from None
            # An incomplete or altered replica never causes a silent rewrite.
            try:
                with saved.open("rb") as handle:
                    raw = handle.read(16385)
                if len(raw) > 16384 or json.loads(raw) != expected:
                    raise LearningError("validation_replica_manifest_changed")
            except (OSError, ValueError):
                raise LearningError("validation_replica_manifest_changed") from None
        else:
            for name, data in fixture.files:
                path = target / name
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("xb") as handle:
                    handle.write(data)
            with saved.open("xb") as handle:
                handle.write(canonical_json(expected).encode("utf8"))
        for row in manifest["files"]:
            path = target / row["path"]
            if (
                _is_link(path)
                or any(_is_link(parent) for parent in path.parents if parent.is_relative_to(target))
                or not path.resolve().is_relative_to(target)
            ):
                raise LearningError("validation_replica_path_unsafe")
            try:
                with path.open("rb") as handle:
                    raw = handle.read(MAX_FIXTURE_BYTES + 1)
            except OSError as error:
                raise LearningError("validation_replica_input_changed") from error
            if len(raw) != row["size_bytes"] or hashlib.sha256(raw).hexdigest() != row["sha256"]:
                raise LearningError("validation_replica_input_changed")
        return ValidationReplica(target, manifest, digest, expected)
