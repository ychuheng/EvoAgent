"""Housekeeping hints are scoped, source checked and never lifecycle actions."""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import select

from evoagent.db.models import LearningSourceRecord, SkillRecord, SkillVersionRecord
from evoagent.skills.library import SkillLibrarySuggestions
from evoagent.skills.trials import TrialScope
from tests.integration.test_skill_merge_proposals import merge_body

pytest_plugins = ("tests.integration.test_personal_trials",)


async def test_similar_old_methods_are_only_hints_and_revoked_sources_are_excluded(trial_candidate):
    client, db, _, _, _, skill = trial_candidate
    body = await merge_body(trial_candidate)
    ids = {UUID(p["skill_id"]) for p in body["parents"]}
    async with db.session_factory() as session:
        for row in await session.scalars(select(SkillRecord).where(SkillRecord.id.in_(ids))):
            row.created_at = datetime.now(UTC) - timedelta(days=31)
        await session.commit()
    service = SkillLibrarySuggestions(db.session_factory)
    scope = TrialScope(skill.workspace_id)
    before = await service.inspect(scope)
    assert len(before["merge_hints"]) == 1
    assert before["merge_hints"][0]["reason"] == "lexical_similarity"
    assert {item["skill_id"] for item in before["low_usage"]} == ids
    assert not before["automatically_changed"] and not before["semantic_equivalence_established"]
    assert not before["low_usage_means_invalid"]
    assert not (await service.inspect(TrialScope(uuid4())))["merge_hints"]
    response = await client.get(
        "/api/v1/skills/library-suggestions", params={"workspace_id": str(skill.workspace_id)}
    )
    assert response.status_code == 200 and len(response.json()["merge_hints"]) == 1
    async with db.session_factory() as session:
        for row in await session.scalars(select(SkillRecord).where(SkillRecord.id.in_(ids))):
            assert row.active_version_id is None and str(row.status) == "enabled"
        assert len(list(await session.scalars(select(SkillVersionRecord)))) == 2
        source = await session.scalar(select(LearningSourceRecord))
        source.status = "revoked"
        source.revocation_epoch += 1
        await session.commit()
    after = await service.inspect(scope)
    assert after["merge_hints"] == after["low_usage"] == []
    assert after["unavailable_count"] == 2


async def test_large_catalog_reports_incomplete_coverage_instead_of_unbounded_scan(trial_candidate):
    _, db, _, _, original, skill = trial_candidate
    from evoagent.skills.canonical import content_hash

    async with db.session_factory() as session:
        for index in range(21):
            row = SkillRecord(
                id=UUID(int=index + 1),
                workspace_id=skill.workspace_id,
                name=f"unlinked {index}",
                slug=f"unlinked_{index}",
                description="unlinked fixture",
            )
            session.add(row)
            await session.flush()
            session.add(
                SkillVersionRecord(
                    skill_id=row.id,
                    version=1,
                    schema_version=original.schema_version,
                    definition=original.definition,
                    content_hash=original.content_hash,
                    extraction_key=content_hash({"library_fixture": index}),
                )
            )
        await session.commit()
    result = await SkillLibrarySuggestions(db.session_factory).inspect(
        TrialScope(skill.workspace_id)
    )
    assert result["truncated"] and result["examined_count"] == 20
    assert result["unavailable_count"] == 20
    assert result["merge_hints"] == result["low_usage"] == []
