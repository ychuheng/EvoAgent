import traceback
from types import SimpleNamespace
from uuid import uuid4

import pytest

from evoagent.db.models import SkillRecord, SkillVersionRecord
from evoagent.skills.access import SkillAccessError, SkillAccessPolicy
from evoagent.skills.canonical import content_hash


async def test_malformed_definition_does_not_survive_in_formatted_exception_chain():
    workspace = uuid4()
    body = {"name": ["private-definition-marker"], "schema_version": 1}
    version = SimpleNamespace(
        skill_id=uuid4(),
        definition=body,
        content_hash=content_hash(body),
        schema_version=1,
    )
    skill = SimpleNamespace(workspace_id=workspace, project_id=None)

    class MetadataSession:
        async def get(self, model, identity):
            if model is SkillVersionRecord:
                return version
            assert model is SkillRecord
            return skill

    with pytest.raises(SkillAccessError, match="skill_definition_invalid") as raised:
        await SkillAccessPolicy().check(MetadataSession(), uuid4(), workspace_id=workspace)
    assert "private-definition-marker" not in "".join(traceback.format_exception(raised.value))
    assert raised.value.__suppress_context__
