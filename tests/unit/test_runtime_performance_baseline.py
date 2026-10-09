import hashlib

import pytest

from scripts.runtime_performance_baseline import measure, percentile


def test_percentile_uses_nearest_rank():
    assert percentile([5, 1, 4, 2, 3], 0.5) == 3
    assert percentile([5, 1, 4, 2, 3], 0.95) == 5


async def test_baseline_refuses_product_database_before_connecting(tmp_path):
    with pytest.raises(ValueError, match="isolated"):
        await measure("postgresql+asyncpg://test:test@localhost/production", repeats=3, deltas=1)
    path = tmp_path / "evoagent_perf_existing.db"
    path.write_bytes(b"existing database must remain untouched")
    before = hashlib.sha256(path.read_bytes()).digest()
    with pytest.raises(ValueError, match="new evoagent_perf"):
        await measure(f"sqlite+aiosqlite:///{path}", repeats=3, deltas=1)
    assert hashlib.sha256(path.read_bytes()).digest() == before


async def test_baseline_counts_real_persistent_task_cost(tmp_path):
    report = await measure(
        f"sqlite+aiosqlite:///{tmp_path / 'evoagent_perf_test.db'}", repeats=3, deltas=1
    )
    assert report["paid_calls"] == 0
    assert report["repeats"] == 3 and len(report["runs"]) == 3
    assert report["script_sha256"]
    for row in report["runs"]:
        assert row["sql_total"] > row["sql_insert"] > 0
        assert row["transactions_commit"] > 0
        assert row["events"] > 0 and row["snapshots"] > 0
        assert row["runtime_config_hash"].startswith("sha256:")
