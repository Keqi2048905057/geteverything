from agent.config import LLMConfig

from .openai_compat import OpenAICompatProvider


class DeepSeekProvider(OpenAICompatProvider):
    """
    DeepSeek Provider。
    当前使用 OpenAI-compatible 接口。
    后续如果 DeepSeek 有特殊参数，再在这里单独处理。
    """

    def __init__(self, config: LLMConfig):
        if not config.base_url:
            config.base_url = "https://api.deepseek.com/v1"
        super().__init__(config)
