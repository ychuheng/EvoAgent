"""Bounded user-download snapshots, never an artifact injection capability."""

import asyncio
import hashlib
import io
import os
import stat
import time
import zipfile
from pathlib import Path

from evoagent.learning.replicas import _is_link
from evoagent.memory.schema import MemoryError
from evoagent.privacy.redaction import detect_sensitive
from evoagent.skills.canonical import canonical_json

MAX_OUTPUT_FILES = 64
MAX_OUTPUT_ENTRIES = 256
MAX_OUTPUT_BYTES = 2 * 1024 * 1024
OUTPUT_SCAN_SECONDS = 5


def _snapshot(root: Path, provenance: dict):
    deadline = time.monotonic() + OUTPUT_SCAN_SECONDS
    files, total, entries = [], 0, 0

    def check_path(path):
        if time.monotonic() > deadline:
            raise MemoryError("validation_output_scan_timeout")
        if any(_is_link(part) for part in (path, *path.parents)):
            raise MemoryError("validation_output_link_rejected")

    def visit(directory):
        nonlocal total, entries
        check_path(directory)
        with os.scandir(directory) as iterator:
            for entry in iterator:
                entries += 1
                if entries > MAX_OUTPUT_ENTRIES:
                    raise MemoryError("validation_output_entry_bound")
                path = Path(entry.path)
                check_path(path)
                relative = path.relative_to(root).as_posix()
                if relative == ".validation-input-manifest.json":
                    continue
                if (
                    len(relative) > 512
                    or len(path.relative_to(root).parts) > 8
                    or "\\" in relative
                    or ":" in relative
                    or any(ord(char) < 32 for char in relative)
                    or detect_sensitive(relative)
                ):
                    raise MemoryError("validation_output_path_rejected")
                if entry.is_dir(follow_symlinks=False):
                    visit(path)
                    continue
                if not entry.is_file(follow_symlinks=False):
                    raise MemoryError("validation_output_not_regular")
                if len(files) >= MAX_OUTPUT_FILES:
                    raise MemoryError("validation_output_file_bound")
                descriptor = os.open(
                    path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
                )
                with os.fdopen(descriptor, "rb") as stream:
                    before = os.fstat(stream.fileno())
                    if not stat.S_ISREG(before.st_mode):
                        raise MemoryError("validation_output_not_regular")
                    data = stream.read(MAX_OUTPUT_BYTES - total + 1)
                    after = os.fstat(stream.fileno())
                check_path(path)
                current = path.stat(follow_symlinks=False)

                def identity(item):
                    return (item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns)

                if identity(before) != identity(after) or identity(after) != identity(current):
                    raise MemoryError("validation_output_changed")
                total += len(data)
                if total > MAX_OUTPUT_BYTES:
                    raise MemoryError("validation_output_byte_bound")
                files.append((relative, data))

    visit(root)
    rows = [
        {"path": name, "size_bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        for name, data in sorted(files)
    ]
    manifest = {"schema_version": 1, **provenance, "files": rows, "total_bytes": total}
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_STORED) as archive:
        for name, data in [
            ("manifest.json", canonical_json(manifest).encode()),
            *[(f"files/{name}", data) for name, data in sorted(files)],
        ]:
            # Fixed timestamps and permissions make recovery of identical bytes deterministic.
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.external_attr = 0o100600 << 16
            archive.writestr(info, data)
    return buffer.getvalue(), manifest


async def snapshot_outputs(root, provenance):
    try:
        return await asyncio.to_thread(_snapshot, root, provenance)
    except MemoryError:
        raise
    except (OSError, ValueError):
        # File-system exception strings can include user paths or filenames.
        raise MemoryError("validation_output_unavailable") from None


async def archive_outputs(guard, service):
    project = await guard.execution_project()
    if project is None:
        return None
    async with guard.factory() as session:
        from sqlalchemy import select

        from evoagent.db.models import ValidationReplicaBindingRecord

        binding = await session.scalar(
            select(ValidationReplicaBindingRecord).where(
                ValidationReplicaBindingRecord.run_id == guard.run_id
            )
        )
    provenance = {
        "request_id": str(binding.request_id),
        "run_id": str(binding.run_id),
        "binding_id": str(binding.id),
        "case_key": binding.case_key,
        "arm": binding.arm,
        "repeat": binding.repeat_index,
        "input_fingerprint": binding.input_fingerprint,
        "input_manifest_hash": binding.manifest_hash,
    }
    content, manifest = await snapshot_outputs(project.root, provenance)
    await guard.check()
    record = await service.create_or_replace(
        run_id=guard.run_id,
        name="validation-output.zip",
        content=content,
        artifact_type="validation_output",
        attributes={
            "content_type": "application/zip",
            "user_download_only": True,
            "output_manifest": manifest,
        },
    )
    await guard.check()
    return record
