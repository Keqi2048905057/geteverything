from abc import ABC, abstractmethod
from typing import Dict, List


class BaseLLMProvider(ABC):
    """
    所有模型供应商的统一基类。
    Agent 层不关心具体模型来自哪里，只调用 chat()。
    """

    @abstractmethod
    def chat(self, messages: List[Dict[str, str]]) -> str:
        pass

    @abstractmethod
    def health_check(self) -> dict:
        pass
