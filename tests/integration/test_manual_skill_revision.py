"""Manual revisions retain source references; they never activate themselves."""

from copy import deepcopy

import pytest
from sqlalchemy import select

from evoagent.db.models import SkillSourceRecord, SkillVersionRecord
from evoagent.skills.access import SkillAccessError, SkillAccessPolicy
from evoagent.skills.lifecycle import SkillVersionStatus
from evoagent.skills.schema import SkillDefinition
from evoagent.skills.service import SkillService
from evoagent.skills.validation import SkillDefinitionValidator
from evoagent.tools.catalog import default_skill_tool_catalog

pytest_plugins = ("tests.integration.test_personal_trials",)


async def test_manual_revision_inherits_exact_sources_and_revocation(trial_candidate):
    client, db, _, state, parent, skill = trial_candidate
    definition = deepcopy(parent.definition)
    definition["steps"][0]["instruction"] += "; explicitly verify the output format"
    service = SkillService(
        db.session_factory,
        SkillDefinitionValidator(
            default_skill_tool_catalog(),
            allowed_tools=frozenset({"file_read"}),
            supported_schema_version=2,
        ),
    )
    revised = await service.create_version(
        skill_id=skill.id,
        definition=SkillDefinition.model_validate(definition),
        parent_version_id=parent.id,
    )
    repeated = await service.create_version(
        skill_id=skill.id,
        definition=SkillDefinition.model_validate(definition),
        parent_version_id=parent.id,
    )
    assert revised.id == repeated.id
    assert (
        revised.parent_version_id == parent.id
        and revised.lifecycle_status is SkillVersionStatus.DRAFT
    )
    async with db.session_factory() as session:
        links = list(
            await session.scalars(
                select(SkillSourceRecord).where(
                    SkillSourceRecord.skill_version_id.in_((parent.id, revised.id))
                )
            )
        )
        original = [row for row in links if row.skill_version_id == parent.id]
        copied = [row for row in links if row.skill_version_id == revised.id]

        def identity(row):
            return (
                row.source_kind,
                row.source_run_id,
                row.learning_source_id,
                row.source_eval_run_id,
                row.trace_artifact_id,
                row.source_trace_hash,
            )

        assert original and {identity(row) for row in original} == {identity(row) for row in copied}
        assert (await session.get(SkillVersionRecord, parent.id)).definition == parent.definition
        assert await SkillAccessPolicy().check(session, revised.id, workspace_id=skill.workspace_id)
    revoked = await client.post(
        f"/api/v1/learning-sources/{state['source']['id']}/revoke",
        json={"reason": "withdraw original provenance", "expected_status": "valid"},
    )
    assert revoked.status_code == 200, revoked.text
    async with db.session_factory() as session:
        with pytest.raises(SkillAccessError):
            await SkillAccessPolicy().check(session, revised.id, workspace_id=skill.workspace_id)
