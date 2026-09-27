"""M6 S-04：人工发布 / 禁用 / 回滚前的**审查记录**与可核对的发布闸门。

计划 §11 S-04 原文：

> 审查来源、diff、评测与安全影响，发布版本并监测；效果退化可禁用/回滚。
> 完成判据：用户能解释"它学到了什么、帮了什么、失败在哪里"。

机制部分（`SkillService.approve` / `reject` / `set_status` / `rollback`、GateReport、审计决策行）
已经存在；这里补的是**人工审查这一层**：审查必须落成一条结构化记录，
能把来源、diff、评测与安全影响四项逐一对应到证据，并如实回答"学到了什么、帮了什么、
失败在哪里"。

三条硬约束（代码判定，不是建议）：

1. **结论强度不得高于评测证据**：评测判定是 `exploratory_only` / `inconclusive` /
   `not_claimable` 时，审查记录里不允许出现"已确认收益"这类说法；
2. **较强收益结论必须有第三方复核**：`claim_scope="confirmed_benefit"` 要求
   `reviewer_role="third_party"`（与计划 §5.4 的 ≥20% 预留一致）；
3. **安全扫描发现项必须逐条处置**：Skill 文本里出现凭据样式、私有绝对路径或
   holdout 数据集/夹具名时，登记为发现项，未处置即不得发布。

本模块不连接数据库、不做时间读取，只做纯函数的检查与渲染，便于测试与人工复核。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

PUBLISH = "publish"
DISABLE = "disable"
ROLLBACK = "rollback"
ACTIONS = (PUBLISH, DISABLE, ROLLBACK)

SELF_REVIEW = "implementer_self_review"
THIRD_PARTY = "third_party"
REVIEWER_ROLES = (SELF_REVIEW, THIRD_PARTY)

# 审查维度：计划 S-04 要求逐项审查来源、diff、评测与安全影响。
DIMENSIONS = ("sources", "diff", "evaluation", "security")

CLAIM_CONFIRMED = "confirmed_benefit"
CLAIM_EXPLORATORY = "exploratory_only"
CLAIM_NONE = "no_effect_claim"
CLAIM_REGRESSION = "regression"
CLAIM_SCOPES = (CLAIM_CONFIRMED, CLAIM_EXPLORATORY, CLAIM_NONE, CLAIM_REGRESSION)

# 评测判定 → 允许出现的结论强度。
VERDICT_TO_CLAIM = {
    "positive_confirmed": CLAIM_CONFIRMED,
    "exploratory_only": CLAIM_EXPLORATORY,
    "inconclusive": CLAIM_NONE,
    "not_claimable": CLAIM_NONE,
    "negative_confirmed": CLAIM_REGRESSION,
}

PLACEHOLDERS = ("todo", "tbd", "待填", "待补", "略", "n/a", "xxx")
MIN_SECTION_CHARS = 20

# 凭据样式与私有路径：只做"发现"，是否可接受由人工在记录里逐条处置。
_CREDENTIAL_PATTERNS = (
    re.compile(r"(?i)\b(api[_-]?key|secret|password|passwd|token)\b\s*[:=]\s*\S+"),
    re.compile(r"\bsk-[A-Za-z0-9]{16,}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{16,}\b"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
)
_PRIVATE_PATH_PATTERNS = (
    re.compile(r"[A-Za-z]:\\Users\\[^\\\s]+", re.IGNORECASE),
    re.compile(r"/(?:home|Users)/[A-Za-z0-9._-]+"),
)
_LEAKAGE_PATTERNS = (
    re.compile(r"m[67]-[a-z0-9-]*holdout[a-z0-9-]*", re.IGNORECASE),
    re.compile(r"ledger-holdout|beacon-holdout"),
)


class ReviewError(ValueError):
    """审查记录不满足发布/禁用/回滚的前置条件。"""


@dataclass(frozen=True, slots=True)
class ReviewCheck:
    name: str
    passed: bool
    detail: str


@dataclass(frozen=True, slots=True)
class SecurityFinding:
    code: str
    detail: str


def scan_skill_text(text: str) -> tuple[SecurityFinding, ...]:
    """静态检查 Skill 文本：凭据样式、私有绝对路径、holdout 泄漏。"""

    findings: list[SecurityFinding] = []
    for pattern in _CREDENTIAL_PATTERNS:
        for match in pattern.finditer(text):
            findings.append(
                SecurityFinding("credential_like_text", f"疑似凭据：{match.group(0)[:40]}")
            )
    for pattern in _PRIVATE_PATH_PATTERNS:
        for match in pattern.finditer(text):
            findings.append(
                SecurityFinding("private_absolute_path", f"私有绝对路径：{match.group(0)}")
            )
    for pattern in _LEAKAGE_PATTERNS:
        for match in pattern.finditer(text):
            findings.append(
                SecurityFinding("holdout_reference", f"提到 holdout 数据集/夹具：{match.group(0)}")
            )
    # 同类发现去重，保留稳定顺序。
    seen: set[tuple[str, str]] = set()
    unique: list[SecurityFinding] = []
    for item in findings:
        key = (item.code, item.detail)
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return tuple(unique)


@dataclass(frozen=True, slots=True)
class EvaluationEvidence:
    """发布所依据的评测证据（来自冻结的 GateReport 与该次配对实验判定）。"""

    experiment_id: str
    verdict: str
    gate_passed: bool
    gate_report_hash: str
    dataset_sha256: str
    report_path: str


@dataclass(frozen=True, slots=True)
class ReviewSubject:
    skill_id: str
    skill_name: str
    version_id: str
    action: str
    skill_text: str = ""
    from_version_id: str | None = None
    target_version_status: str | None = None


@dataclass(frozen=True, slots=True)
class ReviewRecord:
    """人工审查记录：四段解释 + 四个审查维度 + 处置与监测承诺。"""

    subject: ReviewSubject
    reviewer: str
    reviewer_role: str
    learned: str
    helped: str
    failed: str
    claim_scope: str
    dimensions: dict[str, tuple[str, ...]] = field(default_factory=dict)
    evaluation: EvaluationEvidence | None = None
    risk_level: str = ""
    approval_points: tuple[str, ...] = ()
    unmitigated_risks: tuple[str, ...] = ()
    acknowledged_findings: tuple[str, ...] = ()
    monitoring_plan: str = ""
    rollback_plan: str = ""
    reason: str = ""


def _is_filled(text: str) -> bool:
    stripped = text.strip()
    if len(stripped) < MIN_SECTION_CHARS:
        return False
    return not any(placeholder in stripped.lower() for placeholder in PLACEHOLDERS)


def _check(checks: list[ReviewCheck], name: str, passed: bool, detail: str) -> None:
    checks.append(ReviewCheck(name=name, passed=passed, detail=detail))


def validate_review(record: ReviewRecord) -> list[ReviewCheck]:
    """逐项检查审查记录；任一项不通过即不得执行该动作。"""

    checks: list[ReviewCheck] = []
    subject = record.subject

    _check(
        checks,
        "action_is_known",
        subject.action in ACTIONS,
        f"动作 {subject.action} 必须是 {list(ACTIONS)} 之一",
    )
    _check(
        checks,
        "reviewer_recorded",
        bool(record.reviewer.strip()) and record.reviewer_role in REVIEWER_ROLES,
        f"审查者 {record.reviewer!r}（角色 {record.reviewer_role!r}）",
    )

    missing_sections = [
        name
        for name, text in (
            ("learned", record.learned),
            ("helped", record.helped),
            ("failed", record.failed),
        )
        if not _is_filled(text)
    ]
    _check(
        checks,
        "explainability_sections",
        not missing_sections,
        "「学到了什么 / 帮了什么 / 失败在哪里」都已具体写明"
        if not missing_sections
        else f"以下小节写得不足（少于 {MIN_SECTION_CHARS} 字或仍是占位符）：{missing_sections}；"
        "计划 S-04 的完成判据就是用户能解释这三件事",
    )

    empty_dimensions = [name for name in DIMENSIONS if not record.dimensions.get(name)]
    _check(
        checks,
        "review_dimensions_present",
        not empty_dimensions,
        "来源、diff、评测、安全影响四项都附了证据"
        if not empty_dimensions
        else f"缺少证据的审查维度：{empty_dimensions}（每项至少要写清看了什么文件/哈希）",
    )

    evaluation = record.evaluation
    if evaluation is None:
        _check(checks, "evaluation_evidence_bound", False, "没有绑定评测证据")
    else:
        expected = VERDICT_TO_CLAIM.get(evaluation.verdict)
        consistent = expected is not None and record.claim_scope == expected
        _check(
            checks,
            "evaluation_evidence_bound",
            evaluation.gate_passed and bool(evaluation.gate_report_hash.strip()),
            "评测门禁通过且记录了报告哈希"
            if evaluation.gate_passed and evaluation.gate_report_hash.strip()
            else f"评测门禁未通过或缺少报告哈希（gate_passed={evaluation.gate_passed}）",
        )
        _check(
            checks,
            "claim_scope_matches_verdict",
            consistent,
            f"结论强度 {record.claim_scope} 与评测判定 {evaluation.verdict} 一致"
            if consistent
            else f"结论强度 {record.claim_scope} 与评测判定 {evaluation.verdict} 不符"
            f"（应写 {expected}）：不得让审查结论强于证据",
        )

    if record.claim_scope == CLAIM_CONFIRMED:
        _check(
            checks,
            "third_party_review_for_strong_claim",
            record.reviewer_role == THIRD_PARTY,
            "较强收益结论有第三方复核"
            if record.reviewer_role == THIRD_PARTY
            else "自审不得支撑「已确认收益」：只能报告带自评限制的探索性结果",
        )

    findings = scan_skill_text(subject.skill_text)
    unhandled = [
        item.detail for item in findings if f"{item.code}" not in record.acknowledged_findings
    ]
    _check(
        checks,
        "security_findings_handled",
        not unhandled,
        f"安全扫描 {len(findings)} 项发现全部已处置"
        if not unhandled
        else f"未处置的发现：{unhandled}",
    )
    _check(
        checks,
        "risk_declared",
        bool(record.risk_level.strip()),
        f"已声明最大风险等级 {record.risk_level!r}",
    )
    _check(
        checks,
        "no_unmitigated_risk_on_publish",
        subject.action != PUBLISH or not record.unmitigated_risks,
        "没有未缓解风险"
        if not record.unmitigated_risks
        else f"仍有未缓解风险，不得发布：{list(record.unmitigated_risks)}",
    )

    if subject.action == PUBLISH:
        _check(
            checks,
            "monitoring_plan_present",
            _is_filled(record.monitoring_plan),
            "写了发布后的监测计划" if _is_filled(record.monitoring_plan) else "缺少发布后监测计划",
        )
        _check(
            checks,
            "rollback_plan_present",
            _is_filled(record.rollback_plan),
            "写了回滚方案" if _is_filled(record.rollback_plan) else "缺少回滚方案",
        )
        _check(
            checks,
            "claim_is_not_regression",
            record.claim_scope != CLAIM_REGRESSION,
            "评测未显示退步"
            if record.claim_scope != CLAIM_REGRESSION
            else "评测判定为显著退步：应禁用或回滚，而不是发布",
        )
    elif subject.action == DISABLE:
        _check(
            checks,
            "disable_reason_present",
            _is_filled(record.reason),
            "写了禁用原因"
            if _is_filled(record.reason)
            else "禁用必须写明原因（效果退化或安全事件）",
        )
    elif subject.action == ROLLBACK:
        target_ok = (
            subject.target_version_status == "retired" and subject.from_version_id is not None
        )
        _check(
            checks,
            "rollback_target_is_retired",
            target_ok,
            "回滚目标版本已 retired" if target_ok else "回滚目标必须是已 retired 的版本",
        )
        _check(
            checks,
            "rollback_has_accepted_gate",
            evaluation is not None and bool(evaluation.gate_report_hash.strip()),
            "回滚目标带已接受的评测报告哈希"
            if evaluation is not None and evaluation.gate_report_hash.strip()
            else "回滚目标缺少已接受的评测报告，不能回滚到它",
        )
        _check(
            checks,
            "disable_reason_present",
            _is_filled(record.reason),
            "写了回滚原因" if _is_filled(record.reason) else "回滚必须写明原因",
        )
    return checks


def review_passed(checks: list[ReviewCheck]) -> bool:
    return all(item.passed for item in checks)


def assert_review_allows(record: ReviewRecord) -> None:
    """审查不通过则抛出带全部失败项的错误；产品入口在调用 SkillService 前用它。"""

    checks = validate_review(record)
    if not review_passed(checks):
        failed = "；".join(item.detail for item in checks if not item.passed)
        raise ReviewError(f"人工审查未通过（{record.subject.action}）：{failed}")


def record_from_dict(payload: dict[str, Any]) -> ReviewRecord:
    """从 JSON 记录构造；缺字段按缺省处理，由 `validate_review` 报告而不是在这里猜。"""

    subject_raw = payload.get("subject", {})
    subject = ReviewSubject(
        skill_id=str(subject_raw.get("skill_id", "")),
        skill_name=str(subject_raw.get("skill_name", "")),
        version_id=str(subject_raw.get("version_id", "")),
        action=str(subject_raw.get("action", "")),
        skill_text=str(subject_raw.get("skill_text", "")),
        from_version_id=subject_raw.get("from_version_id"),
        target_version_status=subject_raw.get("target_version_status"),
    )
    evaluation_raw = payload.get("evaluation")
    evaluation = (
        EvaluationEvidence(
            experiment_id=str(evaluation_raw.get("experiment_id", "")),
            verdict=str(evaluation_raw.get("verdict", "")),
            gate_passed=bool(evaluation_raw.get("gate_passed")),
            gate_report_hash=str(evaluation_raw.get("gate_report_hash", "")),
            dataset_sha256=str(evaluation_raw.get("dataset_sha256", "")),
            report_path=str(evaluation_raw.get("report_path", "")),
        )
        if isinstance(evaluation_raw, dict)
        else None
    )
    dimensions_raw = payload.get("dimensions", {})
    dimensions = {
        str(name): tuple(str(item) for item in items)
        for name, items in dimensions_raw.items()
        if isinstance(items, list)
    }
    return ReviewRecord(
        subject=subject,
        reviewer=str(payload.get("reviewer", "")),
        reviewer_role=str(payload.get("reviewer_role", "")),
        learned=str(payload.get("learned", "")),
        helped=str(payload.get("helped", "")),
        failed=str(payload.get("failed", "")),
        claim_scope=str(payload.get("claim_scope", "")),
        dimensions=dimensions,
        evaluation=evaluation,
        risk_level=str(payload.get("risk_level", "")),
        approval_points=tuple(str(item) for item in payload.get("approval_points", ())),
        unmitigated_risks=tuple(str(item) for item in payload.get("unmitigated_risks", ())),
        acknowledged_findings=tuple(str(item) for item in payload.get("acknowledged_findings", ())),
        monitoring_plan=str(payload.get("monitoring_plan", "")),
        rollback_plan=str(payload.get("rollback_plan", "")),
        reason=str(payload.get("reason", "")),
    )


def render_review(record: ReviewRecord) -> str:
    """渲染成给人读的审查页：先回答"它学到了什么、帮了什么、失败在哪里"。"""

    subject = record.subject
    lines = [
        f"# Skill 审查记录：{subject.skill_name or subject.skill_id}",
        "",
        f"- 动作：**{subject.action}**",
        f"- 版本：`{subject.version_id}`"
        + (f"（自 `{subject.from_version_id}`）" if subject.from_version_id else ""),
        f"- 审查者：{record.reviewer}（{record.reviewer_role}）",
        f"- 结论强度：**{record.claim_scope}**",
        f"- 最大风险等级：{record.risk_level or '未声明'}",
        "",
        "## 它学到了什么",
        "",
        record.learned.strip() or "_未填写_",
        "",
        "## 它帮了什么",
        "",
        record.helped.strip() or "_未填写_",
        "",
        "## 它在哪里失败",
        "",
        record.failed.strip() or "_未填写_",
        "",
        "## 审查证据",
        "",
    ]
    titles = {
        "sources": "来源",
        "diff": "diff",
        "evaluation": "评测",
        "security": "安全影响",
    }
    for name in DIMENSIONS:
        lines.append(f"### {titles[name]}")
        lines.append("")
        evidence = record.dimensions.get(name) or ()
        if evidence:
            lines.extend(f"- {item}" for item in evidence)
        else:
            lines.append("- _未填写_")
        lines.append("")
    evaluation = record.evaluation
    lines += [
        "## 评测依据",
        "",
        (
            f"- 实验 `{evaluation.experiment_id}`，判定 `{evaluation.verdict}`，"
            f"门禁 {'通过' if evaluation.gate_passed else '未通过'}，"
            f"报告哈希 `{evaluation.gate_report_hash}`"
            if evaluation is not None
            else "- _未绑定评测证据_"
        ),
        f"- 数据哈希：`{evaluation.dataset_sha256}`" if evaluation is not None else "",
        f"- 报告位置：`{evaluation.report_path}`" if evaluation is not None else "",
        "",
        "## 处置与回滚",
        "",
        f"- 监测计划：{record.monitoring_plan.strip() or '_未填写_'}",
        f"- 回滚方案：{record.rollback_plan.strip() or '_未填写_'}",
        f"- 审批点：{'、'.join(record.approval_points) or '无'}",
        f"- 未缓解风险：{'、'.join(record.unmitigated_risks) or '无'}",
        f"- 已处置的安全发现：{'、'.join(record.acknowledged_findings) or '无'}",
        f"- 原因：{record.reason.strip() or '_未填写_'}",
        "",
        "> 本记录只描述人工审查与证据绑定；效果结论的强度以评测判定为准。"
        "自审记录不能支撑「已确认收益」。",
        "",
    ]
    return "\n".join(line for line in lines if line is not None)
