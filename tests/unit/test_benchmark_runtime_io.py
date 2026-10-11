import hashlib
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.benchmark_runtime_io import FAMILIES, measure, validate_database_url


def test_runtime_matrix_cli_can_be_invoked_as_a_file():
    script = Path(__file__).resolve().parents[2] / "scripts/benchmark_runtime_io.py"
    result = subprocess.run(
        [sys.executable, str(script), "--help"], capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0, result.stderr
    assert "--idle-seconds" in result.stdout


async def test_runtime_matrix_refuses_product_or_existing_databases(tmp_path):
    with pytest.raises(ValueError, match="isolated"):
        validate_database_url("postgresql+asyncpg://test:test@localhost/product")
    path = tmp_path / "evoagent_perf_existing.db"
    path.write_bytes(b"retain this file")
    digest = hashlib.sha256(path.read_bytes()).digest()
    with pytest.raises(ValueError, match="new"):
        await measure(f"sqlite+aiosqlite:///{path}", repeats=3, idle_seconds=0)
    assert hashlib.sha256(path.read_bytes()).digest() == digest


async def test_runtime_matrix_measures_real_task_effects_and_skill_selection(tmp_path):
    report = await measure(
        f"sqlite+aiosqlite:///{tmp_path / 'evoagent_perf_matrix.db'}",
        repeats=3,
        idle_seconds=0,
    )
    assert report["paid_calls"] == 0
    assert not report["slo_sample_sufficient"]
    assert report["persisted_semantics_equal"]
    assert len(report["samples"]) == len(FAMILIES) * 2 * 2 * 3
    assert len(report["summaries"]) == len(FAMILIES) * 2 * 2
    for row in report["samples"]:
        assert row["terminal"] == "completed"
        assert row["sql_total"] > 0 and row["transactions_commit"] > 0
        assert row["committed_cursor_contiguous"]
        assert row["selected_versions"] == (1 if row["family"] == "enabled_skill" else 0)
        if row["family"] == "read_edit_recheck":
            assert row["tool_names"] == ["edit_file", "file_read", "file_read"]
            assert row["effect_status_counts"] == {"committed": 1}
