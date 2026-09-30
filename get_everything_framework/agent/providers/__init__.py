from agent.config import LLMConfig
from agent.errors import LLMConfigError

from .base import BaseLLMProvider
from .deepseek import DeepSeekProvider
from .ollama import OllamaProvider
from .openai_compat import OpenAICompatProvider
from .qwen import QwenProvider


def build_provider(config: LLMConfig) -> BaseLLMProvider:
    provider = config.provider.lower().strip()

    if provider in {"openai", "openai_compat", "openai-compatible"}:
        return OpenAICompatProvider(config)

    if provider == "deepseek":
        return DeepSeekProvider(config)

    if provider in {"qwen", "dashscope", "tongyi"}:
        return QwenProvider(config)

    if provider == "ollama":
        return OllamaProvider(config)

    raise LLMConfigError(f"不支持的 LLM_PROVIDER: {config.provider}")
