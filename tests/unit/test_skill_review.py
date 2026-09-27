"""M6 S-04 人工审查记录的测试：证据绑定、结论强度、安全发现处置与三类动作。

计划 §11 S-04 的完成判据是"用户能解释它学到了什么、帮了什么、失败在哪里"，
并要审查来源、diff、评测与安全影响。这组测试把这些逐条钉住，尤其是
**审查结论不得强于评测证据**与**自审不能支撑已确认收益**两条。
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from evoagent.skills.review import (
    CLAIM_CONFIRMED,
    CLAIM_EXPLORATORY,
    CLAIM_NONE,
    CLAIM_REGRESSION,
    DISABLE,
    PUBLISH,
    ROLLBACK,
    SELF_REVIEW,
    THIRD_PARTY,
    EvaluationEvidence,
    ReviewError,
    ReviewRecord,
    ReviewSubject,
    assert_review_allows,
    record_from_dict,
    render_review,
    review_passed,
    scan_skill_text,
    validate_review,
)

LEARNED = "它学到：先读 src/ledger/summary.py 的 render_summary，再回答数据来源，避免凭文件名猜。"
HELPED = "它帮了：把「数据从哪来」的回答定位到具体函数与排序调用，减少无关文件浏览。"
FAILED = "它失败在：遇到空目录时不给结论，也没有提示需要补充输入。"
MONITOR = "发布后连续跟踪 5 次真实任务的通过率与越权计数，任一次越权即禁用。"
ROLLBACK_PLAN = "如效果退化，回滚到上一 ACTIVE 版本并保留本次判定与报告哈希。"


def evaluation(
    verdict: str = "positive_confirmed",
    *,
    gate_passed: bool = True,
    gate_hash: str = "sha256:abc",
) -> EvaluationEvidence:
    return EvaluationEvidence(
        experiment_id="m6-exp-001",
        verdict=verdict,
        gate_passed=gate_passed,
        gate_report_hash=gate_hash,
        dataset_sha256="f" * 64,
        report_path="docs/evaluations/M6-配对实验-2026-09-27.md",
    )


def record(
    *,
    action: str = PUBLISH,
    claim_scope: str = CLAIM_CONFIRMED,
    reviewer_role: str = THIRD_PARTY,
    skill_text: str = "先读 render_summary，再给出数据来源。",
    evaluation_evidence: EvaluationEvidence | None = None,
    target_status: str = "retired",
    **overrides: object,
) -> ReviewRecord:
    fields: dict[str, object] = {
        "reviewer": "reviewer-1",
        "reviewer_role": reviewer_role,
        "learned": LEARNED,
        "helped": HELPED,
        "failed": FAILED,
        "claim_scope": claim_scope,
        "dimensions": {
            "sources": ("run dev-001（dev 任务，人工确认成功证据）",),
            "diff": ("definition_json 仅新增 stop_conditions 与 counterexamples",),
            "evaluation": ("配对实验 m6-exp-001，p=0.004，报告哈希 sha256:abc",),
            "security": ("只读工具；无写入、无网络、无凭据",),
        },
        "evaluation": evaluation_evidence or evaluation(),
        "risk_level": "R1",
        "approval_points": (),
        "unmitigated_risks": (),
        "acknowledged_findings": (),
        "monitoring_plan": MONITOR,
        "rollback_plan": ROLLBACK_PLAN,
        "reason": "首轮发布：配对实验显示正向差异且无安全退化。",
    }
    fields.update(overrides)
    return ReviewRecord(
        subject=ReviewSubject(
            skill_id="ledger-summary-anchors",
            skill_name="ledger-summary-anchors",
            version_id="11111111-1111-1111-1111-111111111111",
            action=action,
            skill_text=skill_text,
            from_version_id="22222222-2222-2222-2222-222222222222",
            target_version_status=target_status,
        ),
        **fields,  # type: ignore[arg-type]
    )


def by_name(checks) -> dict[str, object]:
    return {item.name: item for item in checks}


def test_complete_publish_record_passes() -> None:
    checks = validate_review(record())
    assert review_passed(checks), [item.detail for item in checks if not item.passed]
    assert_review_allows(record())


@pytest.mark.parametrize("field_name", ["learned", "helped", "failed"])
def test_explainability_sections_are_required(field_name: str) -> None:
    """三段解释缺任何一段都不算审查完成——这正是 S-04 的完成判据。"""

    checks = by_name(validate_review(record(**{field_name: ""})))
    assert not checks["explainability_sections"].passed
    assert field_name in checks["explainability_sections"].detail

    short = by_name(validate_review(record(**{field_name: "待填"})))
    assert not short["explainability_sections"].passed


@pytest.mark.parametrize(
    ("verdict", "allowed_claim"),
    [
        ("positive_confirmed", CLAIM_CONFIRMED),
        ("exploratory_only", CLAIM_EXPLORATORY),
        ("inconclusive", CLAIM_NONE),
        ("not_claimable", CLAIM_NONE),
        ("negative_confirmed", CLAIM_REGRESSION),
    ],
)
def test_claim_scope_must_match_verdict(verdict: str, allowed_claim: str) -> None:
    """结论强度必须与评测判定一致：审查不能把探索性结果说成已确认收益。"""

    good = by_name(
        validate_review(record(claim_scope=allowed_claim, evaluation_evidence=evaluation(verdict)))
    )
    assert good["claim_scope_matches_verdict"].passed

    if allowed_claim != CLAIM_CONFIRMED:
        bad = by_name(
            validate_review(
                record(claim_scope=CLAIM_CONFIRMED, evaluation_evidence=evaluation(verdict))
            )
        )
        assert not bad["claim_scope_matches_verdict"].passed
        assert "不得让审查结论强于证据" in bad["claim_scope_matches_verdict"].detail


def test_strong_claim_requires_third_party_review() -> None:
    self_review = by_name(validate_review(record(reviewer_role=SELF_REVIEW)))
    assert not self_review["third_party_review_for_strong_claim"].passed
    assert "自审不得支撑" in self_review["third_party_review_for_strong_claim"].detail

    third = by_name(validate_review(record(reviewer_role=THIRD_PARTY)))
    assert third["third_party_review_for_strong_claim"].passed


def test_evaluation_evidence_must_be_bound() -> None:
    checks = by_name(validate_review(replace(record(), evaluation=None)))
    assert not checks["evaluation_evidence_bound"].passed

    failed_gate = by_name(
        validate_review(record(evaluation_evidence=evaluation(gate_passed=False)))
    )
    assert not failed_gate["evaluation_evidence_bound"].passed

    no_hash = by_name(validate_review(record(evaluation_evidence=evaluation(gate_hash=""))))
    assert not no_hash["evaluation_evidence_bound"].passed


@pytest.mark.parametrize("dimension", ["sources", "diff", "evaluation", "security"])
def test_every_review_dimension_needs_evidence(dimension: str) -> None:
    dimensions = record().dimensions
    stripped = {**dimensions, dimension: ()}
    checks = by_name(validate_review(record(dimensions=stripped)))
    assert not checks["review_dimensions_present"].passed
    assert dimension in checks["review_dimensions_present"].detail


def test_security_scan_flags_credentials_paths_and_holdout_leaks() -> None:
    findings = scan_skill_text(
        "用 api_key = sk-abcdefghijklmnopqrst 访问 C:\\Users\\someone\\private 项目，"
        "样本取自 m6-skill-holdout-v1.json 与 ledger-holdout。"
    )
    codes = {item.code for item in findings}
    assert codes == {"credential_like_text", "private_absolute_path", "holdout_reference"}
    assert scan_skill_text("先读 render_summary，再给出数据来源。") == ()


def test_unhandled_security_findings_block_publish() -> None:
    text = "token = sk-abcdefghijklmnopqrst"
    blocked = by_name(validate_review(record(skill_text=text)))
    assert not blocked["security_findings_handled"].passed

    acknowledged = by_name(
        validate_review(record(skill_text=text, acknowledged_findings=("credential_like_text",)))
    )
    assert acknowledged["security_findings_handled"].passed


def test_publish_requires_monitoring_rollback_and_no_open_risk() -> None:
    checks = by_name(validate_review(record(monitoring_plan="", rollback_plan="待补")))
    assert not checks["monitoring_plan_present"].passed
    assert not checks["rollback_plan_present"].passed

    risky = by_name(validate_review(record(unmitigated_risks=("未验证写权限边界",))))
    assert not risky["no_unmitigated_risk_on_publish"].passed

    regression = by_name(
        validate_review(
            record(
                claim_scope=CLAIM_REGRESSION, evaluation_evidence=evaluation("negative_confirmed")
            )
        )
    )
    assert not regression["claim_is_not_regression"].passed
    assert "应禁用或回滚" in regression["claim_is_not_regression"].detail


def test_disable_requires_a_reason_but_not_a_monitoring_plan() -> None:
    disabled = record(action=DISABLE, claim_scope=CLAIM_NONE, monitoring_plan="", rollback_plan="")
    checks = by_name(validate_review(disabled))
    assert checks["disable_reason_present"].passed
    assert "monitoring_plan_present" not in checks
    assert "no_unmitigated_risk_on_publish" in checks  # 该检查只对 publish 生效且当前通过

    no_reason = by_name(validate_review(record(action=DISABLE, reason="")))
    assert not no_reason["disable_reason_present"].passed


def test_rollback_requires_a_retired_target_with_an_accepted_gate() -> None:
    rolled = record(
        action=ROLLBACK,
        claim_scope=CLAIM_REGRESSION,
        evaluation_evidence=evaluation("negative_confirmed"),
    )
    assert review_passed(validate_review(rolled))

    checks = by_name(validate_review(record(action=ROLLBACK, target_status="active")))
    assert not checks["rollback_target_is_retired"].passed

    no_gate = by_name(
        validate_review(record(action=ROLLBACK, evaluation_evidence=evaluation(gate_hash="")))
    )
    assert not no_gate["rollback_has_accepted_gate"].passed


def test_reviewer_must_be_recorded_with_a_known_role() -> None:
    checks = by_name(validate_review(record(reviewer="", reviewer_role="someone")))
    assert not checks["reviewer_recorded"].passed
    assert not by_name(validate_review(record(action="archive")))["action_is_known"].passed


def test_assert_review_allows_lists_every_failure() -> None:
    with pytest.raises(ReviewError, match="人工审查未通过"):
        assert_review_allows(record(learned="", claim_scope=CLAIM_NONE))


def test_record_from_dict_round_trip_and_render() -> None:
    original = record()
    payload = {
        "subject": {
            "skill_id": original.subject.skill_id,
            "skill_name": original.subject.skill_name,
            "version_id": original.subject.version_id,
            "action": original.subject.action,
            "skill_text": original.subject.skill_text,
            "from_version_id": original.subject.from_version_id,
            "target_version_status": original.subject.target_version_status,
        },
        "reviewer": original.reviewer,
        "reviewer_role": original.reviewer_role,
        "learned": original.learned,
        "helped": original.helped,
        "failed": original.failed,
        "claim_scope": original.claim_scope,
        "dimensions": {name: list(items) for name, items in original.dimensions.items()},
        "evaluation": {
            "experiment_id": original.evaluation.experiment_id,
            "verdict": original.evaluation.verdict,
            "gate_passed": original.evaluation.gate_passed,
            "gate_report_hash": original.evaluation.gate_report_hash,
            "dataset_sha256": original.evaluation.dataset_sha256,
            "report_path": original.evaluation.report_path,
        },
        "risk_level": original.risk_level,
        "approval_points": list(original.approval_points),
        "unmitigated_risks": list(original.unmitigated_risks),
        "acknowledged_findings": list(original.acknowledged_findings),
        "monitoring_plan": original.monitoring_plan,
        "rollback_plan": original.rollback_plan,
        "reason": original.reason,
    }
    parsed = record_from_dict(payload)
    assert review_passed(validate_review(parsed))

    markdown = render_review(parsed)
    assert "它学到了什么" in markdown
    assert "它帮了什么" in markdown
    assert "它在哪里失败" in markdown
    assert "安全影响" in markdown
    assert "自审记录不能支撑" in markdown


def test_render_marks_missing_sections_instead_of_hiding_them() -> None:
    markdown = render_review(record(learned="", dimensions={"sources": ()}))
    assert "_未填写_" in markdown
