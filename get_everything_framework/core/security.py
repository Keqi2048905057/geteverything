"""会话密钥与安全基线（M2）。

方案 M2 交付项之一：「移除默认 ``dev-secret-key``」。

策略：

* ``Config.SECRET_KEY`` 的默认值已被清空，仓库中不再存在可预测的固定密钥；
* 若 ``.env`` 提供了足够强的 ``SECRET_KEY``，直接使用；
* 若缺失、仍为历史默认值 ``dev-secret-key``、或长度过短，则**不复用弱值**，
  改为生成一次性随机密钥，并在启动时打印明确提示；
* 一次性密钥意味着「重启后所有登录会话失效」——这是安全与便利之间
  刻意选择的取舍，本机联调阶段接受。

不把随机密钥自动写进 ``.env``：程序静默改写用户本地配置文件不可接受，
改为打印生成命令，由使用者自行决定。
"""

from __future__ import annotations

import secrets
import warnings

from config import Config

# 历史默认值：一旦出现，视为「未配置」而不是「已配置」。
INSECURE_DEFAULTS = {"dev-secret-key", "changeme", "secret", "flask-secret"}

# 会话签名密钥的最小可接受长度（Flask 用 HMAC-SHA1，短密钥存在被爆破风险）。
MIN_SECRET_LENGTH = 16

_EPHEMERAL_SECRET: str | None = None


def is_weak_secret(value: str | None) -> bool:
    """判断给定密钥是否为弱值/未配置。"""
    text = (value or "").strip()
    if not text:
        return True
    if text.lower() in INSECURE_DEFAULTS:
        return True
    return len(text) < MIN_SECRET_LENGTH


def generate_secret() -> str:
    """生成一个新的随机会话密钥（不会自动写入任何文件）。"""
    return secrets.token_urlsafe(32)


def resolve_secret_key() -> str:
    """返回本次进程实际使用的会话签名密钥。

    同一次进程内多次调用返回同一个值（缓存），保证会话在进程存活期间有效。
    """
    global _EPHEMERAL_SECRET

    configured = (Config.SECRET_KEY or "").strip()
    if not is_weak_secret(configured):
        return configured

    if _EPHEMERAL_SECRET is None:
        _EPHEMERAL_SECRET = generate_secret()
        warnings.warn(
            "未检测到强 SECRET_KEY，已为本进程生成一次性会话密钥："
            "重启后所有登录会话都会失效。"
            '如需固定，请执行 python -c "import secrets; print(secrets.token_urlsafe(32))" '
            "并把结果写入 .env 的 SECRET_KEY。",
            RuntimeWarning,
            stacklevel=2,
        )
    return _EPHEMERAL_SECRET


def secret_key_is_ephemeral() -> bool:
    """当前会话密钥是否为进程内临时生成。"""
    return is_weak_secret((Config.SECRET_KEY or "").strip())


def security_report() -> dict:
    """安全基线状态（只返回状态字符串，不返回密钥本身）。"""
    return {
        "secret_key": "ephemeral" if secret_key_is_ephemeral() else "configured",
        "session_cookie_httponly": True,
        "session_cookie_samesite": "Lax",
        "bind_host_default": Config.WEB_HOST,
        "debug_enabled": bool(Config.WEB_DEBUG),
    }
