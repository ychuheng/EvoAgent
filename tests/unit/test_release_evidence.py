import hashlib
import importlib.util
from pathlib import Path


def checker():
    path = Path(__file__).resolve().parents[2] / "scripts/phase4_release_check.py"
    spec = importlib.util.spec_from_file_location("release_check", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_missing_and_pending_evidence_never_means_release_ready(tmp_path):
    module = checker()
    errors, pending = module.verify(tmp_path, {"checks": {"real_embedding": {"status": "pending"}}})
    assert "missing:docker" in errors and pending == ["real_embedding"]


def test_changed_or_absent_artifact_cannot_pass(tmp_path):
    module = checker()
    path = tmp_path / "report.json"
    path.write_bytes(b"{}\n")
    artifact = {"path": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    manifest = {
        "checks": {name: {"status": "passed", "artifacts": [artifact]} for name in module.REQUIRED}
    }
    assert module.verify(tmp_path, manifest) == ([], [])
    path.write_bytes(b"{}\r\n")
    assert module.verify(tmp_path, manifest) == ([], [])
    path.write_text('{"changed":true}')
    assert "hash_mismatch:docker" in module.verify(tmp_path, manifest)[0]
    path.unlink()
    assert "missing_artifact:docker" in module.verify(tmp_path, manifest)[0]
