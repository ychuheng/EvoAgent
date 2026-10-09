"""Deterministic offline learning demo; it is not evidence of model capability."""

from evoagent.skills.schema import SkillDefinition


class PersonalDemoGenerator:
    async def generate(self, sources, *, context=None):
        context = context or {}
        name = context.get("required_name") or "verify_before_concluding"
        corrections = [
            item.get("correction", "")
            for source in sources
            for item in source.payload.get("user_corrections", ())
        ]
        return SkillDefinition.model_validate(
            {
                "schema_version": 2,
                "name": name,
                "description": "核验输入与工具结果，区分观察证据和未验证结论",
                "triggers": ["核验", "验证", "检查"],
                "preconditions": {"allowed_tools": ["file_read"], "max_effective_risk": "R0"},
                "steps": [
                    {
                        "id": "verify",
                        "action": "model",
                        "instruction": (
                            "先确认输入与授权范围，使用允许的工具核验，列出证据与未知项。"
                        )
                        + "；".join(corrections),
                    }
                ],
                "success_criteria": ["结论能对应可观察证据，未知项明确说明"],
                "validators": ["run_completed"],
                "applicability": {"task_families": ["general"]},
                "rationale": "从单次执行和用户纠正提出方法，实际效果仍须验证。",
                "stop_conditions": ["输入或工具证据不足时停止推断并请求补充"],
                "counterexamples": [
                    {
                        "situation": "假设反例：资料不存在或授权已撤销",
                        "why_not": "不能把模型陈述当作已验证事实",
                        "origin": "hypothetical",
                    }
                ],
            }
        )
