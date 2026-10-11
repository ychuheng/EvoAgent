"""One paired-evidence decision shared by collection and human review."""


def business_verification(items):
    business = [
        item for item in items if item.get("arm") == "treatment" and item.get("business_criterion")
    ]
    if any(item.get("verdict") == "fail" for item in business):
        return "failed"
    return (
        "passed"
        if business and all(item.get("verdict") == "pass" for item in business)
        else "pending"
    )


def comparison_verification(items):
    """Permit a repaired positive control failure, never an unknown or unsafe control."""
    pairs = {}
    for item in items:
        key = (item.get("case_key"), item.get("repeat"), item.get("criterion_id"))
        if None in key or item.get("arm") not in {"control", "treatment"}:
            return "pending"
        pair = pairs.setdefault(key, {})
        if item["arm"] in pair:
            return "pending"
        pair[item["arm"]] = item
    if not pairs:
        return "pending"
    improved = False
    for pair in pairs.values():
        if set(pair) != {"control", "treatment"}:
            return "pending"
        control, treatment = pair["control"], pair["treatment"]
        if any(
            item.get("comparable") is not True
            or item.get("independent_input") is not True
            or item.get("judge_origin") not in {"machine", "user"}
            or not item.get("judge_id")
            or item.get("verdict") not in {"pass", "fail"}
            for item in pair.values()
        ):
            return "pending"
        if (control.get("case_kind"), control.get("business_criterion")) != (
            treatment.get("case_kind"),
            treatment.get("business_criterion"),
        ):
            return "pending"
        if treatment["verdict"] != "pass":
            return "failed"
        if control["verdict"] == "fail":
            if (
                treatment.get("case_kind") != "positive"
                or treatment.get("business_criterion") is not True
            ):
                return "failed"
            improved = True
    return "improved" if improved else "equivalent"
