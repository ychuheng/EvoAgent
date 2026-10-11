import pytest
from fastapi import HTTPException

from evoagent.api.routes.skill_trials import respond
from evoagent.skills.trials import TrialError


@pytest.mark.parametrize(
    "code",
    [
        "trial_reactivation_requires_fresh_validation",
        "trial_rollback_requires_fresh_validation",
    ],
)
async def test_stale_validation_is_a_state_conflict(code):
    async def failed():
        raise TrialError(code)

    with pytest.raises(HTTPException) as result:
        await respond(failed())
    assert result.value.status_code == 409
    assert result.value.detail == {"code": code}
