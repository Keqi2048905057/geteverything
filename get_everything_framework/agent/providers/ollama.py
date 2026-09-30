from agent.config import LLMConfig

from .openai_compat import OpenAICompatProvider


class OllamaProvider(OpenAICompatProvider):
    """
    Ollama 本地模型 Provider。
    默认使用 Ollama OpenAI-compatible API。
    """

    def __init__(self, config: LLMConfig):
        if not config.base_url:
            config.base_url = "http://127.0.0.1:11434/v1"

        if not config.api_key:
            config.api_key = "ollama"

        super().__init__(config)
