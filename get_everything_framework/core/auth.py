"""本地管理员认证（M1 最小可用版）。

本机联调版的认证约定（方案第 3.2 节）：

* Token 存在本地 ``.env`` 或本地配置；
* 首次启动打印随机 Token 或引导设置；
* 登录后使用 HttpOnly Session；
* 所有修改类 API 必须校验 Session；
* 自动化调用可以使用 ``X-Local-Token``；
* 这不是完整 RBAC，但不能匿名扫描和改密钥。

M1 只做「单一管理员 Token + Session + X-Local-Token」，
用户表 / 多账号 / 审计留到 M2。
"""

from __future__ import annotations

import hmac
import secrets

from flask import g, request, session

from config import Config
from core.errors import UnauthenticatedError

SESSION_KEY = "local_admin"
TOKEN_HEADER = "X-Local-Token"
TOKEN_FORM_FIELD = "token"

# 未配置 LOCAL_ADMIN_TOKEN 时，进程启动生成的一次性 Token。
# 只存在于内存，重启即失效；启动时打印到控制台供本机使用。
_EPHEMERAL_TOKEN: str | None = None


def get_admin_token() -> str:
    """返回当前生效的管理员 Token。

    优先使用 ``.env`` 中的 ``LOCAL_ADMIN_TOKEN``；未配置时生成并缓存
    一个进程级一次性 Token（方案允许「首次启动打印随机 Token」）。
    """
    global _EPHEMERAL_TOKEN
    configured = (Config.LOCAL_ADMIN_TOKEN or "").strip()
    if configured:
        return configured
    if _EPHEMERAL_TOKEN is None:
        _EPHEMERAL_TOKEN = secrets.token_urlsafe(24)
    return _EPHEMERAL_TOKEN


def is_ephemeral_token() -> bool:
    """当前 Token 是否为进程内临时生成（未落到 .env）。"""
    return not (Config.LOCAL_ADMIN_TOKEN or "").strip()


def verify_token(candidate: str | None) -> bool:
    """常量时间比较 Token，避免时序侧信道。"""
    if not candidate:
        return False
    return hmac.compare_digest(str(candidate), get_admin_token())


def _extract_token() -> str | None:
    """从请求头 / 表单 / JSON 体中提取候选 Token。"""
    header_value = request.headers.get(TOKEN_HEADER)
    if header_value:
        return header_value
    if request.method in {"POST", "PUT", "PATCH"}:
        if request.form.get(TOKEN_FORM_FIELD):
            return request.form.get(TOKEN_FORM_FIELD)
        payload = request.get_json(silent=True)
        if isinstance(payload, dict) and payload.get(TOKEN_FORM_FIELD):
            return str(payload[TOKEN_FORM_FIELD])
    return None


def is_authenticated() -> bool:
    """当前请求是否已通过管理员认证（Session 或 Token 头）。"""
    if session.get(SESSION_KEY) is True:
        return True
    return verify_token(_extract_token())


def login_with_token(candidate: str | None) -> bool:
    """校验 Token 并写入 Session。成功返回 True。"""
    if not verify_token(candidate):
        return False
    session[SESSION_KEY] = True
    session.permanent = False
    return True


def logout() -> None:
    """清除当前会话的管理员标记。"""
    session.pop(SESSION_KEY, None)


def require_admin() -> None:
    """认证守卫。

    Raises:
        UnauthenticatedError: 未携带有效的 Session 或 Token。
    """
    if not is_authenticated():
        raise UnauthenticatedError(
            "需要本地管理员登录：请携带 X-Local-Token 请求头，或先通过登录页建立会话",
            details={"header": TOKEN_HEADER},
        )


def mark_request_flags() -> None:
    """把认证状态挂到 ``flask.g``，供模板与日志读取。"""
    g.is_local_admin = is_authenticated()


def login_hint() -> dict:
    """启动时打印用的提示信息。

    Returns:
        dict: ``{"token": ..., "ephemeral": bool}``。
        仅用于本地控制台，禁止放进 API 响应。
    """
    return {
        "token": get_admin_token(),
        "ephemeral": is_ephemeral_token(),
    }
