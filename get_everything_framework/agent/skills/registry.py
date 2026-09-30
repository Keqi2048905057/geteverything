from typing import List, Optional

from agent.skills.base import AgentSkill
from agent.skills.osint_recon import OSINT_RECON_SKILL


ALL_SKILLS: List[AgentSkill] = [
    OSINT_RECON_SKILL,
]


def get_enabled_skills() -> List[AgentSkill]:
    """
    获取所有启用状态的 Skill。

    后续扩展 Skill 时，只需要：
    1. 新增一个 skill_xxx.py；
    2. 在本文件 import；
    3. 加入 ALL_SKILLS。
    """
    return sorted(
        [skill for skill in ALL_SKILLS if skill.enabled],
        key=lambda item: item.priority,
    )


def get_skill_by_id(skill_id: str) -> Optional[AgentSkill]:
    """
    根据 Skill ID 获取指定 Skill。
    """
    for skill in ALL_SKILLS:
        if skill.id == skill_id:
            return skill
    return None


def build_enabled_skills_prompt() -> str:
    """
    构建所有启用 Skill 的 system prompt 片段。
    """
    enabled_skills = get_enabled_skills()
    if not enabled_skills:
        return ""

    skill_prompts = [
        "# 已启用 Agent Skills\n"
        "下面是当前 Agent 可以使用的 Skill。"
        "你需要根据用户意图选择合适的 Skill，"
        "并严格遵守对应 Skill 的工具调用格式和安全边界。\n"
    ]

    for skill in enabled_skills:
        skill_prompts.append(skill.render())

    return "\n".join(skill_prompts)
