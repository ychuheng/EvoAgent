"""反馈意图的确定性路由；混合或未确定意图不自动启动付费提炼。"""

from typing import Literal

from evoagent.learning.schema import FeedbackPayload


def route_feedback(payload: FeedbackPayload) -> Literal["method", "fact", "clarify", "none"]:
    if not payload.learn_from_feedback:
        return "none"
    if payload.intent in {"mixed", "unsure"}:
        return "clarify"
    return payload.intent
