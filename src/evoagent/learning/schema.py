"""客户端只能提交意图和证据引用，身份与序号由服务端生成。"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class FeedbackPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: Literal["method", "fact", "unsure", "mixed"]
    verdict: Literal["helpful", "needs_fix", "incorrect"]
    comment: str = Field(default="", max_length=8000)
    correction: str = Field(default="", max_length=8000)
    evidence_refs: list[dict[str, Any]] = Field(default_factory=list, max_length=100)
    learn_from_feedback: bool = False


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
