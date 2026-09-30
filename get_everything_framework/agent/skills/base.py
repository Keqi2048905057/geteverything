from dataclasses import dataclass
from typing import List


@dataclass
class AgentSkill:
    """
    Agent Skill 基础结构。

    每个 Skill 都应该描述：
    1. 自己能做什么；
    2. 什么时候触发；
    3. 可以调用哪些工具；
    4. 调用工具时需要遵守什么规则。
    """

    id: str
    name: str
    version: str
    description: str
    triggers: List[str]
    tools: List[str]
    prompt: str
    enabled: bool = True
    priority: int = 100

    def render(self) -> str:
        """
        渲染成最终注入 system prompt 的 Skill 文本。
        """
        if not self.enabled:
            return ""

        triggers_text = "、".join(self.triggers)
        tools_text = "、".join(self.tools)

        return f"""
==============================
Skill ID: {self.id}
Skill Name: {self.name}
Skill Version: {self.version}
Skill Description: {self.description}
Skill Triggers: {triggers_text}
Allowed Tools: {tools_text}
==============================

{self.prompt}
""".strip()
