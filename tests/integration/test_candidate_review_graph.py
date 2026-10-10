"""Review acknowledges evidence only while the entire source graph is valid."""

import pytest
from sqlalchemy import select

from evoagent.db.models import SkillRecord, SkillSourceRecord
from evoagent.skills.lifecycle import SkillStatus

pytest_plugins = ("tests.integration.test_personal_trials",)


@pytest.mark.parametrize("revocation", ["disabled", "missing_source"])
async def test_review_rejects_invalid_source_graph(trial_candidate, revocation):
    client, db, _, state, version, skill = trial_candidate
    async with db.session_factory() as session:
        if revocation == "disabled":
            parent = await session.get(SkillRecord, skill.id)
            parent.status = SkillStatus.DISABLED
        else:
            link = await session.scalar(
                select(SkillSourceRecord).where(SkillSourceRecord.skill_version_id == version.id)
            )
            await session.delete(link)
        await session.commit()
    result = await client.post(
        f"/api/v1/learning-requests/{state['id']}/review",
        json={
            "expected_lock_version": state["lock_version"],
            "reason": "review lineage",
            "action": "acknowledge",
        },
    )
    assert result.status_code == 422, result.text
    assert "candidate_source_graph_invalid" in result.text
    current = (await client.get(f"/api/v1/learning-requests/{state['id']}")).json()
    assert current["status"] == "ready_for_review"
    assert current["lock_version"] == state["lock_version"]
