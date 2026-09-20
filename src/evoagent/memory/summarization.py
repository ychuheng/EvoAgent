"""无工具、无递归的确定性摘要：保留原话摘录和完整归档引用。"""

from evoagent.core.models import MessageRole
from evoagent.memory.policy import redact
from evoagent.sessions.service import text_hash


class ExtractiveSummarizer:
    """默认不调用额外模型，辅助模型 token 成本为零，输出有硬上限。"""

    version = "extractive-v1"
    max_input_chars = 1_000_000
    max_groups = 128
    max_quote_chars = 96

    def summarize(self, messages, groups):
        if sum(len(m.content or "") for m in messages) > self.max_input_chars:
            raise ValueError("summary_input_limit")
        if len(groups) > self.max_groups:
            raise ValueError("summary_group_limit")
        observations = []
        covered = []
        for group in groups:
            group_id = text_hash("|".join(messages[i].model_dump_json() for i in group))
            covered.append(group_id)
            for index in group:
                message = messages[index]
                if message.role is MessageRole.TOOL:
                    observations.append(
                        {
                            "group_id": group_id,
                            "quote": redact(message.content or "")[: self.max_quote_chars],
                            "source_index": index,
                        }
                    )
        return {
            "schema_version": 1,
            "method": self.version,
            "goal_ref": "original_user_messages",
            "constraint_refs": [
                i
                for i, m in enumerate(messages)
                if m.role in (MessageRole.SYSTEM, MessageRole.USER)
            ],
            "observations": observations,
            "open_questions": [],
            "completed_steps": [],
            "pending_steps": [],
            "covered_group_ids": covered,
            "authority": "untrusted_observations_not_tool_or_approval_status",
        }
