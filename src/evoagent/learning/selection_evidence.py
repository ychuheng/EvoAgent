"""Verify actual frozen injection; an experimental target is not adoption."""

from sqlalchemy import select

from evoagent.db.models import RunSkillSelectionRecord
from evoagent.learning.schema import LearningError
from evoagent.runtime.run_config import RunConfigSnapshot
from evoagent.skills.canonical import content_hash


async def actual_selection_evidence(session, run, *, workspace_id, target_id, project_id=None):
    if run.config_snapshot is None:
        return {"verified": False, "applied": None, "reason": "runtime_config_unavailable"}
    try:
        config = RunConfigSnapshot.model_validate(run.config_snapshot)
    except ValueError:
        raise LearningError("validation_selection_binding_invalid") from None
    if (
        config.schema_version != 3
        or config.content_hash() != run.config_hash
        or config.under_test_skill_version_id != target_id
    ):
        raise LearningError("validation_selection_binding_invalid")
    bindings = list(
        await session.scalars(
            select(RunSkillSelectionRecord).where(RunSkillSelectionRecord.run_id == run.id)
        )
    )
    selections = config.selected_skills
    if len(bindings) != len(selections):
        raise LearningError("validation_selection_binding_invalid")
    selected = selections[0] if selections else None
    if selected is not None:
        binding = bindings[0]
        if (
            selected.scope.workspace_id != workspace_id
            or selected.scope.project_id != project_id
            or binding.skill_version_id != selected.version_id
            or binding.content_hash != selected.content_hash
            or binding.rendered_hash != selected.rendered_hash
            or binding.scope_key != content_hash(selected.scope.model_dump(mode="json"))
            or binding.origin != selected.origin
            or binding.trial_id != selected.trial_id
            or binding.selection_policy_version != config.selector_version
            or binding.applicability.get("status") != "applicable"
        ):
            raise LearningError("validation_selection_binding_invalid")
    return {
        "verified": True,
        "applied": selected is not None,
        "selection": selected.model_dump(mode="json") if selected else None,
        "selection_hash": config.skill_selection_hash,
        "selector_version": config.selector_version,
        "run_config_hash": run.config_hash,
    }


def adoption_contract_passed(items, candidate_id):
    """Human business verdicts cannot override the measured adoption decision."""
    treatment = [item for item in items if item.get("arm") == "treatment"]
    if {item.get("case_kind") for item in treatment} != {"positive", "counterexample"}:
        return False
    for item in treatment:
        evidence = item.get("actual_selection", {})
        if evidence.get("verified") is not True:
            return False
        if item["case_kind"] == "positive":
            if evidence.get("applied") is not True or (evidence.get("selection") or {}).get(
                "version_id"
            ) != str(candidate_id):
                return False
        elif evidence.get("applied") is not False:
            return False
    return True
