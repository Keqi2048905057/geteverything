from dataclasses import replace
from typing import Dict, List, Optional

from .config import LLMConfig, load_llm_config, validate_llm_config
from .providers import build_provider


class LLMClient:
    """
    模型调用统一入口。
    对 AgentAction 保持兼容：chat(messages) -> str。
    """

    def __init__(
        self,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout: Optional[int] = None,
        config: Optional[LLMConfig] = None,
    ):
        loaded = config or load_llm_config()
        self.config = replace(
            loaded,
            model_id=model or loaded.model_id,
            api_key=api_key or loaded.api_key,
            base_url=base_url or loaded.base_url,
            timeout=float(timeout) if timeout is not None else loaded.timeout,
        )
        validate_llm_config(self.config)
        self.provider = build_provider(self.config)

    def chat(self, messages: List[Dict[str, str]]) -> str:
        return self.provider.chat(messages)

    def health_check(self) -> dict:
        return self.provider.health_check()


OpenAICompatibleClient = LLMClient
