from evoagent.skills.library import similarity


def test_shared_dsl_structure_and_short_text_are_not_merge_evidence():
    assert (
        similarity(
            {"name": "same", "steps": [{"instruction": "read file"}]},
            {"name": "same", "steps": [{"instruction": "read file"}]},
        )
        == 0
    )
    left = {"description": "preserve identifiers as strings before joining tables"}
    right = {"description": "rebuild docker images after changing dependencies and locks"}
    assert similarity(left, right) < 0.65
    renamed = {**left, "name": "completely different identifier"}
    assert similarity(left, renamed) == 1
