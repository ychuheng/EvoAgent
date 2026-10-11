"""Exercise the real downgrade predicates without destructive DDL."""

import importlib.util
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text


@pytest.mark.parametrize("table", ["skill_trials", "skill_observations"])
def test_0026_refuses_any_retained_trial_evidence(tmp_path, monkeypatch, table):
    path = Path("migrations/versions/20261009_0026_personal_skill_trials.py")
    spec = importlib.util.spec_from_file_location("trial_guard", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_engine(f"sqlite:///{tmp_path / 'guard.db'}")
    try:
        with engine.begin() as connection:
            connection.execute(text("CREATE TABLE skill_trials (status TEXT)"))
            connection.execute(text("CREATE TABLE skill_observations (status TEXT)"))
            connection.execute(text(f"INSERT INTO {table} VALUES ('replaced')"))
            monkeypatch.setattr(migration.op, "get_bind", lambda: connection)
            with pytest.raises(RuntimeError, match="personal trial evidence"):
                migration.downgrade()
            assert connection.scalar(text(f"SELECT count(*) FROM {table}")) == 1
    finally:
        engine.dispose()


@pytest.mark.parametrize("kind", ["dataset", "purpose", "comparison", "learning_request"])
def test_0027_refuses_personal_validation_evidence(tmp_path, monkeypatch, kind):
    path = Path("migrations/versions/20261009_0027_personal_validation_arms.py")
    spec = importlib.util.spec_from_file_location("validation_guard", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_engine(f"sqlite:///{tmp_path / 'guard.db'}")
    try:
        with engine.begin() as connection:
            connection.execute(text("CREATE TABLE eval_datasets (purpose TEXT)"))
            connection.execute(
                text(
                    "CREATE TABLE eval_experiments "
                    "(purpose TEXT, comparison_version_id TEXT, learning_request_id TEXT)"
                )
            )
            if kind == "dataset":
                connection.execute(text("INSERT INTO eval_datasets VALUES ('personal')"))
            else:
                connection.execute(
                    text("INSERT INTO eval_experiments VALUES (:p,:c,:r)"),
                    {
                        "p": "personal" if kind == "purpose" else "formal",
                        "c": "fixture" if kind == "comparison" else None,
                        "r": "fixture" if kind == "learning_request" else None,
                    },
                )
            monkeypatch.setattr(migration.op, "get_bind", lambda: connection)
            with pytest.raises(RuntimeError, match="personal validation evidence"):
                migration.downgrade()
    finally:
        engine.dispose()
