import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .errors import LLMConfigError


@dataclass
class LLMConfig:
    provider: str
    model_id: str
    api_key: str
    base_url: str
    timeout: float = 60.0
    max_retries: int = 2
    temperature: float = 0.0
    max_tokens: Optional[int] = 1024
    json_mode: bool = False


_ENV_LOADED = False


def _load_local_env_once() -> None:
    global _ENV_LOADED
    if _ENV_LOADED:
        return

    current = Path(__file__).resolve()
    env_candidates = [
        current.parent / ".env",
        current.parent.parent / ".env",
    ]

    for env_path in env_candidates:
        if not env_path.exists():
            continue

        for raw_line in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or line.startswith(">") or "=" not in line:
                continue

            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and value and os.getenv(key) is None:
                os.environ[key] = value

    _ENV_LOADED = True


def _get_env(primary: str, fallback: Optional[str] = None, default: Optional[str] = None) -> str:
    value = os.getenv(primary)
    if value:
        return value.strip()

    if fallback:
        value = os.getenv(fallback)
        if value:
            return value.strip()

    return default or ""


def _to_int(value: str, default: int) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _to_float(value: str, default: float) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _to_bool(value: str, default: bool = False) -> bool:
    if value is None:
        return default

    lowered = str(value).strip().lower()
    if lowered in {"1", "true", "yes", "on"}:
        return True
    if lowered in {"0", "false", "no", "off"}:
        return False

    return default


def load_llm_config() -> LLMConfig:
    _load_local_env_once()

    model_id = _get_env("LLM_MODEL_ID", "MODEL_ID") or _get_env("DEEPSEEK_MODEL")
    api_key = _get_env("LLM_API_KEY", "API_KEY") or _get_env("DEEPSEEK_API_KEY")
    base_url = _get_env("LLM_BASE_URL", "BASE_URL") or _get_env("DEEPSEEK_API_URL")

    return LLMConfig(
        provider=_get_env("LLM_PROVIDER", default="openai_compat"),
        model_id=model_id,
        api_key=api_key,
        base_url=base_url,
        timeout=_to_float(_get_env("LLM_TIMEOUT", default="60"), 60.0),
        max_retries=_to_int(_get_env("LLM_MAX_RETRIES", default="2"), 2),
        temperature=_to_float(_get_env("LLM_TEMPERATURE", default="0"), 0.0),
        max_tokens=_to_int(_get_env("LLM_MAX_TOKENS", default="1024"), 1024),
        json_mode=_to_bool(_get_env("LLM_JSON_MODE", default="false"), False),
    )


def validate_llm_config(config: LLMConfig) -> None:
    missing = []
    provider = (config.provider or "").strip().lower()

    if not config.model_id:
        missing.append("LLM_MODEL_ID 或 MODEL_ID")

    if provider != "ollama" and not config.api_key:
        missing.append("LLM_API_KEY 或 API_KEY")

    providers_with_default_base_url = {"deepseek", "qwen", "dashscope", "tongyi", "ollama"}
    if not config.base_url and provider not in providers_with_default_base_url:
        missing.append("LLM_BASE_URL 或 BASE_URL")

    if missing:
        raise LLMConfigError("模型配置不完整，缺少：" + "、".join(missing))

    if config.base_url and not config.base_url.startswith(("http://", "https://")):
        raise LLMConfigError("LLM_BASE_URL / BASE_URL 必须以 http:// 或 https:// 开头")

    if config.timeout <= 0:
        raise LLMConfigError("LLM_TIMEOUT 必须大于 0")

    if config.max_retries < 0:
        raise LLMConfigError("LLM_MAX_RETRIES 不能小于 0")
