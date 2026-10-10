"""An identical byte digest cannot authorize a quarantined or erased artifact."""

import pytest

from evoagent.config import Settings
from evoagent.db.models import ArtifactRecord
from evoagent.privacy.artifact_access import ArtifactInjectionGuard, ArtifactSensitiveContent
from evoagent.privacy.scanner import shared_scanner
from evoagent.tools.base import ToolPermissionError
from tests.integration.test_artifact_injection_guard import CLEAN_TEXT, _add_artifact, _environment


async def test_cache_reuses_scan_only_and_off_restores_original_calls(tmp_path, monkeypatch):
    database, aggregate, artifacts, store, _ = await _environment(tmp_path)
    try:
        record = await _add_artifact(artifacts, aggregate, CLEAN_TEXT)
        settings = Settings(_env_file=None, runtime_scan_cache_enabled=True)
        guard = ArtifactInjectionGuard(
            session_factory=database.session_factory, artifact_store=store, settings=settings
        )
        scanner = shared_scanner(guard._limits)
        calls = []
        original = scanner.scan

        async def count(text, limits, *, deadline=None):
            calls.append(len(text))
            return await original(text, limits, deadline=deadline)

        monkeypatch.setattr(scanner, "scan", count)

        async def reset(status="unchecked", erased=False):
            async with database.session_factory() as session:
                row = await session.get(ArtifactRecord, record.id)
                row.redaction_status = status
                row.redaction_policy_version = 0
                row.redaction_checked_hash = None
                row.attributes = {**row.attributes, "erased": erased}
                await session.commit()

        async def read():
            return await guard.read_verified_text(
                artifact_id=record.id, run_id=aggregate.run.id, purpose="artifact_read"
            )

        assert await read() == CLEAN_TEXT
        await reset()
        assert await read() == CLEAN_TEXT
        assert len(calls) == 1
        await reset("quarantined")
        with pytest.raises(ArtifactSensitiveContent):
            await read()
        await reset(erased=True)
        with pytest.raises(ToolPermissionError):
            await read()
        assert len(calls) == 1
        await reset()
        guard = ArtifactInjectionGuard(
            session_factory=database.session_factory,
            artifact_store=store,
            settings=settings.model_copy(update={"runtime_scan_cache_enabled": False}),
        )
        assert await read() == CLEAN_TEXT
        assert len(calls) == 2
    finally:
        await database.dispose()
