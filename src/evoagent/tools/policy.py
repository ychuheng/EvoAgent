"""所有工具调用共享的权限决策。"""

from dataclasses import dataclass
from enum import StrEnum

from pydantic import BaseModel

from evoagent.core.models import ToolRisk
from evoagent.tools.base import ToolInstance


class PolicyAction(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    REQUIRE_APPROVAL = "require_approval"


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    action: PolicyAction
    effective_risk: ToolRisk
    reason: str


class PermissionPolicy:
    """根据基础风险、动态风险和禁用名单作出统一决定。"""

    def __init__(self, *, denied_tools: frozenset[str] = frozenset()) -> None:
        self._denied_tools = denied_tools

    def evaluate(self, tool: ToolInstance, arguments: BaseModel) -> PolicyDecision:
        effective_risk = tool.risk
        risk_resolver = getattr(tool, "effective_risk", None)
        if callable(risk_resolver):
            effective_risk = risk_resolver(arguments)
        if tool.name in self._denied_tools:
            return PolicyDecision(PolicyAction.DENY, effective_risk, "tool is disabled by policy")
        if effective_risk in (ToolRisk.R0, ToolRisk.R1):
            return PolicyDecision(PolicyAction.ALLOW, effective_risk, "risk is auto-approved")
        return PolicyDecision(
            PolicyAction.REQUIRE_APPROVAL,
            effective_risk,
            "risk requires an explicit user decision",
        )
