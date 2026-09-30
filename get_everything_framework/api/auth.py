"""本地管理员登录 / 登出接口。

路由（与页面配套，挂在 ``/api`` 前缀下）：

* ``POST /api/auth/login``    —— 校验 Token，建立 HttpOnly Session；
* ``POST /api/auth/logout``   —— 清除会话；
* ``GET  /api/auth/session``  —— 查询当前会话状态（前端用来决定显示登录还是退出）。

约定：这三个接口都不返回 Token 明文；只有 ``/api/auth/session`` 会告知
当前 Token 是否为进程内临时生成（便于前端提示「请把 Token 写入 .env」）。
"""

from __future__ import annotations

from flask import jsonify, request

from api import api_bp
from core import audit
from core.auth import (
    TOKEN_FORM_FIELD,
    TOKEN_HEADER,
    is_authenticated,
    is_ephemeral_token,
    login_with_token,
    logout,
)


@api_bp.route("/auth/login", methods=["POST"])
def auth_login():
    """用本地管理员 Token 换取会话。

    请求体（JSON 或表单）:
        {"token": "<LOCAL_ADMIN_TOKEN>"}

    响应:
        200 ``{"ok": true, "authenticated": true}``
        401 ``{"ok": false, "error_code": "unauthenticated", ...}``
    """
    payload = request.get_json(silent=True) or {}
    candidate = payload.get(TOKEN_FORM_FIELD) or request.form.get(TOKEN_FORM_FIELD)
    if not candidate:
        # 也允许直接从请求头带 Token（自动化脚本常见用法）
        candidate = request.headers.get(TOKEN_HEADER)

    if not login_with_token(candidate):
        audit.record(audit.EVENT_LOGIN_FAILED, detail={"path": request.path})
        return (
            jsonify(
                {
                    "ok": False,
                    "error_code": "unauthenticated",
                    "error_message": "Token 无效，请检查 .env 中的 LOCAL_ADMIN_TOKEN",
                }
            ),
            401,
        )

    audit.record(audit.EVENT_LOGIN_SUCCEEDED, detail={"path": request.path})
    return jsonify({"ok": True, "authenticated": True})


@api_bp.route("/auth/logout", methods=["POST"])
def auth_logout():
    """清除当前管理员会话。未登录时也返回成功（幂等）。"""
    logout()
    return jsonify({"ok": True, "authenticated": False})


@api_bp.route("/auth/session", methods=["GET"])
def auth_session():
    """查询当前会话状态，供前端渲染登录态。"""
    authenticated = is_authenticated()
    return jsonify(
        {
            "ok": True,
            "authenticated": authenticated,
            "auth_header": TOKEN_HEADER,
            "token_is_ephemeral": is_ephemeral_token(),
        }
    )
