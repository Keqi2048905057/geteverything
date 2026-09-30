import time
from typing import Any, Dict, List

try:
    from openai import OpenAI
except ImportError:  # pragma: no cover
    OpenAI = None

try:
    import openai as legacy_openai
except ImportError:  # pragma: no cover
    legacy_openai = None

from agent.config import LLMConfig
from agent.errors import (
    LLMAuthError,
    LLMConfigError,
    LLMConnectionError,
    LLMError,
    LLMRateLimitError,
    LLMResponseError,
    LLMServerError,
    LLMTimeoutError,
)

from .base import BaseLLMProvider


class OpenAICompatProvider(BaseLLMProvider):
    def __init__(self, config: LLMConfig):
        self.config = config
        self.client = None
        self._sdk_mode = None

        if OpenAI is not None:
            self.client = OpenAI(
                api_key=config.api_key,
                base_url=config.base_url,
                timeout=config.timeout,
            )
            self._sdk_mode = "modern"
        elif legacy_openai is not None and hasattr(legacy_openai, "ChatCompletion"):
            legacy_openai.api_key = config.api_key
            legacy_openai.api_base = config.base_url
            self._sdk_mode = "legacy_chat"
        else:
            raise LLMConfigError("未安装可用的 openai 依赖，请先执行: pip install -r requirement.txt")

    def chat(self, messages: List[Dict[str, str]]) -> str:
        return self._chat_with_retry(messages)

    def _chat_with_retry(self, messages: List[Dict[str, str]]) -> str:
        last_error = None

        for attempt in range(self.config.max_retries + 1):
            try:
                return self._chat_once(messages)

            except (LLMTimeoutError, LLMConnectionError, LLMServerError) as exc:
                last_error = exc

                if attempt >= self.config.max_retries:
                    break

                time.sleep(min(2 ** attempt, 8))

            except LLMError:
                raise

            except Exception as exc:
                last_error = exc

                if attempt >= self.config.max_retries:
                    break

                time.sleep(min(2 ** attempt, 8))

        raise LLMError(f"模型调用失败，已重试 {self.config.max_retries} 次：{last_error}")

    def _chat_once(self, messages: List[Dict[str, str]]) -> str:
        payload: Dict[str, Any] = {
            "model": self.config.model_id,
            "messages": messages,
            "temperature": self.config.temperature,
        }

        if self.config.max_tokens:
            payload["max_tokens"] = self.config.max_tokens

        if self.config.json_mode:
            payload["response_format"] = {"type": "json_object"}

        try:
            response = self._create_completion(payload)
        except Exception as exc:
            raise self._convert_error(exc)

        return self._extract_content(response)

    def _create_completion(self, payload: Dict[str, Any]) -> Any:
        if self._sdk_mode == "modern":
            return self.client.chat.completions.create(**payload)
        if self._sdk_mode == "legacy_chat":
            return legacy_openai.ChatCompletion.create(**payload)
        raise LLMConfigError("未识别的 openai SDK 模式")

    def _extract_content(self, response: Any) -> str:
        try:
            choices = response["choices"] if isinstance(response, dict) else getattr(response, "choices", None)
            if not choices:
                raise LLMResponseError("模型响应中没有 choices")

            first_choice = choices[0]
            message = first_choice["message"] if isinstance(first_choice, dict) else getattr(first_choice, "message", None)
            if not message:
                raise LLMResponseError("模型响应中没有 message")

            content = message["content"] if isinstance(message, dict) else getattr(message, "content", None)
            if content is None:
                raise LLMResponseError("模型响应 content 为空")

            content = str(content).strip()
            if not content:
                raise LLMResponseError("模型返回了空字符串")

            return content

        except LLMResponseError:
            raise
        except Exception as exc:
            raise LLMResponseError(f"模型响应结构异常：{exc}")

    def _convert_error(self, exc: Exception) -> LLMError:
        message = self._sanitize_error_message(str(exc))
        status_code = self._extract_status_code(exc)

        if status_code in {401, 403}:
            return LLMAuthError(f"模型认证失败：{message}")

        if status_code == 429:
            return LLMRateLimitError(f"模型服务限流：{message}")

        if status_code in {500, 502, 503, 504}:
            return LLMServerError(f"模型服务端异常：{message}")

        if status_code == 404:
            return LLMConfigError(f"模型不存在或 Base URL 错误：{message}")

        if status_code == 400:
            return LLMConfigError(f"模型请求参数错误：{message}")

        lowered = message.lower()
        if "timeout" in lowered or "timed out" in lowered:
            return LLMTimeoutError(f"模型请求超时：{message}")

        if "connection" in lowered or "connect" in lowered:
            return LLMConnectionError(f"模型连接失败：{message}")

        return LLMError(f"模型调用异常：{message}")

    def health_check(self) -> dict:
        try:
            content = self.chat(
                [
                    {"role": "system", "content": "你是模型连通性测试助手。"},
                    {"role": "user", "content": "请只回复 ok"},
                ]
            )

            return {
                "ok": True,
                "provider": self.config.provider,
                "model": self.config.model_id,
                "base_url": self.config.base_url,
                "content": content,
            }

        except Exception as exc:
            return {
                "ok": False,
                "provider": self.config.provider,
                "model": self.config.model_id,
                "base_url": self.config.base_url,
                "error": str(exc),
            }

    def _extract_status_code(self, exc: Exception):
        status_code = getattr(exc, "status_code", None)
        if isinstance(status_code, int):
            return status_code

        http_status = getattr(exc, "http_status", None)
        if isinstance(http_status, int):
            return http_status

        response = getattr(exc, "response", None)
        if response is not None:
            response_status = getattr(response, "status_code", None)
            if isinstance(response_status, int):
                return response_status

        return None

    def _sanitize_error_message(self, message: str) -> str:
        api_key = self.config.api_key or ""
        if api_key and api_key in message:
            return message.replace(api_key, self._mask_secret(api_key))
        return message

    def _mask_secret(self, value: str) -> str:
        if len(value) <= 8:
            return "***"
        return f"{value[:4]}***{value[-4:]}"
