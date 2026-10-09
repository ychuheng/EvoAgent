"""客户端只能提交意图和证据引用，身份与序号由服务端生成。"""

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class FeedbackPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: Literal["method", "fact", "unsure", "mixed"]
    verdict: Literal["helpful", "needs_fix", "incorrect"]
    comment: str = Field(default="", max_length=8000)
    correction: str = Field(default="", max_length=8000)
    evidence_refs: list[dict[str, Any]] = Field(default_factory=list, max_length=100)
    learn_from_feedback: bool = False


class LearningError(ValueError):
    """Stable error codes only; never echo evidence or submitted secrets."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class SourceEligibility(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    run_id: UUID
    workspace_id: UUID
    project_id: UUID | None
    source_role: Literal["personal"] = "personal"
    run_status: str
    feedback_id: UUID | None
    method_authorized: bool
    user_reported_helpful: bool
    project_authorization_version: int | None


class ExperienceEvidence(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    goal: str
    outcome: dict[str, Any]
    verified_facts: tuple[dict[str, Any], ...] = ()
    user_corrections: tuple[dict[str, Any], ...] = ()
    failed_attempts: tuple[dict[str, Any], ...] = ()
    effective_steps: tuple[dict[str, Any], ...] = ()
    selected_versions: tuple[str, ...] = ()
    artifact_refs: tuple[dict[str, Any], ...] = ()
    input_refs: tuple[dict[str, Any], ...] = ()
    tool_manifest_hash: str
    source_role: Literal["personal"] = "personal"
    unknowns: tuple[str, ...] = ()
    redaction_policy_version: int = 0
    redacted: bool = False
    redaction_categories: tuple[str, ...] = ()


class FeedbackSubmission(FeedbackPayload):
    client_request_id: str = Field(min_length=1, max_length=128)
    expected_revision: int | None = Field(default=None, ge=0)


class LearningSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_request_id: str = Field(min_length=1, max_length=128)
    feedback_id: UUID | None = None
    target_skill_id: UUID | None = None
    expected_base_version_id: UUID | None = None


class LearningPolicySubmission(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_lock_version: int = Field(ge=0)
    mode: Literal["off", "manual", "suggest"]
    daily_limit_micros: int | None = Field(default=None, ge=0, le=2147483647)
    request_limit_micros: int | None = Field(default=None, ge=0, le=2147483647)
    daily_candidate_limit: int = Field(default=3, ge=1, le=100)
    cooldown_seconds: int = Field(default=86400, ge=0, le=2592000)
    max_source_risk: Literal["R0", "R1", "R2", "R3"] = "R1"


class FeedbackView(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: UUID
    run_id: UUID
    revision: int
    learning_revision: int
    routing: Literal["method", "fact", "clarify", "none"]
    learning_request_id: UUID | None = None


class LearningRequestView(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: UUID
    workspace_id: UUID
    origin_run_id: UUID
    request_kind: str
    status: str
    stage: str
    lock_version: int
    candidate_version_id: UUID | None
    validation_report_hash: str | None
    error_code: str | None
    available_actions: tuple[str, ...]
    policy_snapshot: dict[str, Any] = Field(default_factory=dict)
    validation_report: dict[str, Any] | None = None
    source: dict[str, Any] | None = None
    cost: dict[str, int] | None = None


class LearningDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_lock_version: int = Field(ge=0)
    reason: str = Field(default="", max_length=2000)


class CandidateReview(LearningDecision):
    action: Literal["acknowledge", "reject"]
    reason: str = Field(min_length=1, max_length=2000)


class LearningRetry(LearningDecision):
    client_request_id: str = Field(min_length=1, max_length=128)


class LearningSourceView(BaseModel):
    model_config = ConfigDict(frozen=True, from_attributes=True)

    id: UUID
    run_id: UUID
    source_revision: str
    artifact_id: UUID
    content_hash: str
    status: str
    revocation_epoch: int


class SourceRevocation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1, max_length=2000)
    expected_status: Literal["valid", "revoked", "erased"]


class LearningCriterion(BaseModel):
    """执行证据与人工判定分别入合同，不接受模型自评来源。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    criterion_id: str = Field(min_length=1, max_length=128)
    source: Literal["machine", "user"]
    expected: Any
    required_evidence: tuple[str, ...] = ()


class CriterionResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    criterion_id: str
    source: Literal["machine", "user"]
    status: Literal["passed", "failed", "pending", "unknown"]
    evidence_refs: tuple[str, ...] = ()
    actor_id: str | None = None


def criteria_passed(
    criteria: tuple[LearningCriterion, ...], results: tuple[CriterionResult, ...]
) -> bool:
    """证据缺失/待人工判定不能通过；结果只匹配其冻结判据。"""

    indexed = {result.criterion_id: result for result in results}
    if not criteria or len(indexed) != len(results):
        return False
    if len({item.criterion_id for item in criteria}) != len(criteria):
        return False
    if set(indexed) != {criterion.criterion_id for criterion in criteria}:
        return False
    for criterion in criteria:
        result = indexed[criterion.criterion_id]
        if result.source != criterion.source or result.status != "passed":
            return False
        if not set(criterion.required_evidence).issubset(result.evidence_refs):
            return False
        if criterion.source == "user" and not result.actor_id:
            return False
    return True
