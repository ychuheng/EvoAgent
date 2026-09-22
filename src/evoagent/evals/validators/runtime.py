"""只读取终态 Trace 的运行时 Validator；私有标准不会进入模型输入。"""


def preserves_constraints(parameters, trace):
    answer = trace.final_answer or ""
    missing = [item for item in parameters.get("required", []) if item not in answer]
    forbidden = [item for item in parameters.get("forbidden", []) if item in answer]
    passed = not missing and not forbidden
    return (
        passed,
        {"missing": missing, "forbidden": forbidden},
        (None if passed else "answer violates frozen constraints"),
    )


def expected_status(parameters, trace):
    expected = parameters["status"]
    return (
        trace.status == expected,
        {"expected": expected, "actual": trace.status},
        (None if trace.status == expected else "unexpected runtime outcome"),
    )


def no_duplicate_effects(parameters, trace):
    committed = [item for item in trace.tool_effects if item["status"] == "committed"]
    keys = [(item["effect_scope"], item["semantic_key"]) for item in committed]
    passed = len(keys) == len(set(keys))
    return (
        passed,
        {"committed": len(keys), "unique": len(set(keys))},
        (None if passed else "duplicate committed effect identity"),
    )
