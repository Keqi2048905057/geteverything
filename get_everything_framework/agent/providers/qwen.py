from agent.config import LLMConfig

from .openai_compat import OpenAICompatProvider


class QwenProvider(OpenAICompatProvider):
    """
    通义千问 Provider。
    可兼容 DashScope OpenAI-compatible 模式。
    """

    def __init__(self, config: LLMConfig):
        if not config.base_url:
            config.base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"
        super().__init__(config)
