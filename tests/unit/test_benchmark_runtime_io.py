import hashlib
import subprocess
import sys
from pathlib import Path

import pytest

from evoagent.core.models import EventType
from evoagent.skills.canonical import content_hash
from scripts.benchmark_runtime_io import FAMILIES, STREAM_CHUNKS, measure, validate_database_url
from scripts.runtime_resource_probe import json_bytes


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
        assert row["event_payload_utf8_bytes"] > 0
        assert row["snapshot_state_utf8_bytes"] >= 0
        assert row["artifact_content_bytes"] >= 0
        assert row["event_loop_lag_samples"] > 0
        assert row["event_loop_lag_max_ms"] >= 0
        assert row["server_lock_wait_ms"] is None
        assert row["query_result_wire_bytes"] is None
        assert 0 <= row["event_buffer_peak_bytes"] <= 64 * 1024
        assert row["scan_cache_hits"] >= 0
        assert row["scan_cache_misses"] >= 0
        assert row["scan_cache_evictions"] >= 0
        if not row["optimized"]:
            assert row["event_buffer_peak_bytes"] == 0
            assert row["scan_cache_hits"] == row["scan_cache_misses"] == 0
        assert row["selected_versions"] == (1 if row["family"] == "enabled_skill" else 0)
        if row["family"] == "read_edit_recheck":
            assert row["tool_names"] == ["edit_file", "file_read", "file_read"]
            assert row["effect_status_counts"] == {"committed": 1}


def test_payload_byte_probe_counts_utf8_content_without_retaining_payload():
    assert json_bytes({"中文": "值"}) == len('{"中文":"值"}'.encode())


async def test_explicit_streaming_fixture_reduces_total_io_without_losing_deltas(tmp_path):
    report = await measure(
        f"sqlite+aiosqlite:///{tmp_path / 'evoagent_perf_stream.db'}",
        families=("streaming_progress",),
        repeats=3,
        idle_seconds=0,
    )
    assert report["complete"] and report["persisted_semantics_equal"]
    assert len(report["samples"]) == 12 and report["paid_calls"] == 0
    expected = content_hash("".join(STREAM_CHUNKS))
    for cache in ("cold_pool", "warm_pool"):
        off = [s for s in report["samples"] if s["cache"] == cache and not s["optimized"]]
        on = [s for s in report["samples"] if s["cache"] == cache and s["optimized"]]
        for row in (*off, *on):
            assert row["model_delta_text_hash"] == expected
            assert row["event_types"].count(EventType.MODEL_DELTA) == 128
            assert row["selected_versions"] == 0 and not row["tool_names"]
        # A substantial reduction, not an exact fragile implementation count.
        assert max(s["sql_total"] for s in on) < min(s["sql_total"] for s in off) / 2
        assert (
            max(s["transactions_commit"] for s in on)
            < min(s["transactions_commit"] for s in off) / 2
        )
