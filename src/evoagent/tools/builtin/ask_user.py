"""通过持久化审批通道向用户提出问题的工具定义。"""

from pydantic import Field

from evoagent.core.models import ContractModel, ToolRisk
from evoagent.tools.base import BaseTool, ToolExecutionError


class AskUserArguments(ContractModel):
    question: str = Field(min_length=1, max_length=2_000)


class AskUserTool(BaseTool[AskUserArguments]):
    name = "ask_user"
    description = "Pause the task and ask the user for missing information."
    arguments_model = AskUserArguments
    risk = ToolRisk.R2
    has_side_effects = False
    parallel_safe = False

    async def invoke(self, arguments: AskUserArguments) -> str:
        raise ToolExecutionError("ask_user must be resolved through the approval service")
