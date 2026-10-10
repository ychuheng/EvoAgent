import io
import json
import os
import zipfile

import pytest

from evoagent.learning import replica_outputs
from evoagent.memory.schema import MemoryError


async def test_download_snapshot_preserves_binary_bytes_and_is_deterministic(tmp_path):
    (tmp_path / ".validation-input-manifest.json").write_text("private internal descriptor")
    (tmp_path / "input.txt").write_bytes(b"edited input\n")
    (tmp_path / "result.bin").write_bytes(b"\x00\xff\x01")
    content, manifest = await replica_outputs.snapshot_outputs(tmp_path, {"arm": "treatment"})
    again, _ = await replica_outputs.snapshot_outputs(tmp_path, {"arm": "treatment"})
    assert content == again
    assert manifest["total_bytes"] == 16
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        assert archive.read("files/result.bin") == b"\x00\xff\x01"
        assert archive.read("files/input.txt") == b"edited input\n"
        assert json.loads(archive.read("manifest.json")) == manifest
        assert not any("validation-input" in name for name in archive.namelist())


@pytest.mark.parametrize(
    ("limit", "code"),
    [
        ("MAX_OUTPUT_BYTES", "byte_bound"),
        ("MAX_OUTPUT_FILES", "file_bound"),
        ("MAX_OUTPUT_ENTRIES", "entry_bound"),
    ],
)
async def test_output_bounds_refuse_incomplete_archive(tmp_path, monkeypatch, limit, code):
    (tmp_path / "a").write_bytes(b"ab")
    (tmp_path / "b").write_bytes(b"cd")
    monkeypatch.setattr(replica_outputs, limit, 1)
    with pytest.raises(MemoryError, match=code):
        await replica_outputs.snapshot_outputs(tmp_path, {})


async def test_scan_deadline_is_explicit(tmp_path, monkeypatch):
    monkeypatch.setattr(replica_outputs, "OUTPUT_SCAN_SECONDS", -1)
    with pytest.raises(MemoryError, match="scan_timeout"):
        await replica_outputs.snapshot_outputs(tmp_path, {})


@pytest.mark.skipif(os.name == "nt", reason="unprivileged Windows cannot create real symlinks")
async def test_link_cannot_export_host_file(tmp_path):
    root = tmp_path / "replica"
    root.mkdir()
    secret = tmp_path / "outside.txt"
    secret.write_text("host content")
    (root / "result.txt").symlink_to(secret)
    with pytest.raises(MemoryError, match="link_rejected"):
        await replica_outputs.snapshot_outputs(root, {})


async def test_changed_file_is_not_silently_snapshotted(tmp_path, monkeypatch):
    target = tmp_path / "result.txt"
    target.write_text("original")
    real_fstat = os.fstat
    calls = 0

    def fstat(descriptor):
        nonlocal calls
        calls += 1
        if calls == 2:
            target.write_text("modified during capture")
        return real_fstat(descriptor)

    monkeypatch.setattr(os, "fstat", fstat)
    with pytest.raises(MemoryError, match="output_changed"):
        await replica_outputs.snapshot_outputs(tmp_path, {})
