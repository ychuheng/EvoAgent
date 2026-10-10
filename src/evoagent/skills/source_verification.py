"""Bounded physical provenance checks outside task fencing transactions.

This dedicated reader verifies evidence; it never makes learning_source or trace
artifacts available through the model's generic artifact_read tool.
"""

import asyncio
from uuid import UUID

from sqlalchemy import update

from evoagent.db.models import ArtifactRecord
from evoagent.learning.schema import LearningError
from evoagent.learning.sources import PersonalSourceService
from evoagent.memory.schema import MemoryError
from evoagent.privacy.artifact_access import ArtifactInjectionGuard, ArtifactSensitiveContent
from evoagent.skills.access import SkillAccessError, SkillAccessPolicy
from evoagent.tools.base import ToolError
from evoagent.trace.artifacts import LocalArtifactStore


class SkillSourceVerifier:
    MAX_ARTIFACTS = 200
    TOTAL_SCAN_SECONDS = 10

    def __init__(self, factory, settings, scope, *, store=None):
        self.factory, self.settings, self.scope = factory, settings, scope
        self.store = store or LocalArtifactStore(settings.artifact_root)

    async def _proofs(self, version_id):
        proofs = {}
        async with self.factory() as session:
            version = await SkillAccessPolicy().check(
                session,
                version_id,
                workspace_id=self.scope.workspace_id,
                project_id=self.scope.project_id,
                source_proofs=proofs,
            )
            digest = version.content_hash
        if len(proofs) > self.MAX_ARTIFACTS:
            raise MemoryError("skill_source_scan_budget_exceeded")
        return digest, proofs

    async def verify(self, version_id: UUID):
        try:
            async with asyncio.timeout(self.TOTAL_SCAN_SECONDS):
                return await self._verify(version_id)
        except MemoryError:
            raise
        except (SkillAccessError, LearningError, ToolError, OSError, ValueError, TimeoutError):
            raise MemoryError("skill_source_verification_failed") from None

    async def _verify(self, version_id):
        digest, proofs = await self._proofs(version_id)
        for proof in proofs.values():
            if proof.source_kind == "personal":
                # This validates the registered sanitized experience, its
                # revocation epoch and current policy, without raw run replay.
                await PersonalSourceService(self.factory, artifact_store=self.store).read_frozen(
                    proof.learning_source_id,
                    expected_revocation_epoch=proof.revocation_epoch,
                )
            else:
                await self._verify_trace(proof)
        current_digest, current_proofs = await self._proofs(version_id)
        if current_digest != digest or current_proofs != proofs:
            raise MemoryError("skill_source_changed_during_scan")
        return digest

    async def _verify_trace(self, proof):
        if proof.size_bytes > self.settings.artifact_scan_inline_max_bytes:
            raise MemoryError("skill_source_scan_budget_exceeded")
        async with asyncio.timeout(self.settings.artifact_scan_wall_ms / 1000):
            raw = await self.store.read_bounded(
                proof.uri, max_bytes=self.settings.artifact_scan_inline_max_bytes
            )
            try:
                await ArtifactInjectionGuard(
                    session_factory=self.factory, settings=self.settings
                ).verify_derived_text(
                    text=raw.decode("utf8"),
                    run_id=proof.run_id,
                    source_id=str(proof.artifact_id),
                    source_hash=proof.content_hash,
                    purpose="skill_source_verify",
                )
            except ArtifactSensitiveContent:
                async with self.factory() as session:
                    await session.execute(
                        update(ArtifactRecord)
                        .where(
                            ArtifactRecord.id == proof.artifact_id,
                            ArtifactRecord.content_hash == proof.content_hash,
                        )
                        .values(redaction_status="quarantined")
                    )
                    await session.commit()
                raise
