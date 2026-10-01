"""统一错误处理器。

把 :class:`core.errors.AppError` 及其子类转换为稳定的 JSON 响应，
保证 API 层「失败也带 error_code」的约定（方案第 6.2 节）。

方案第 19 节：错误是排障的主线索，因此每个错误都额外记一条结构化事件
（自动带当前 ``request_id`` / ``job_id`` 等关联字段）。事件里**只记错误码与
错误消息**，不记请求体（请求体可能含目标列表）。
"""

from __future__ import annotations

from flask import Flask, jsonify, request
from werkzeug.exceptions import HTTPException

from core import observability
from core.errors import AppError, ErrorCode

# HTTP 状态码 → 稳定错误码。避免把 404 一律报成 bad_request。
_STATUS_TO_CODE = {
    400: ErrorCode.BAD_REQUEST,
    401: ErrorCode.UNAUTHENTICATED,
    403: ErrorCode.PERMISSION_DENIED,
    404: ErrorCode.NOT_FOUND,
    405: ErrorCode.BAD_REQUEST,
    413: ErrorCode.BAD_REQUEST,
    415: ErrorCode.BAD_REQUEST,
    429: ErrorCode.RATE_LIMITED,
}


def wants_json() -> bool:
    """判断当前请求是否期望 JSON 响应。"""
    if request.path.startswith("/api/"):
        return True
    if request.is_json:
        return True
    accept = request.accept_mimetypes
    return accept["application/json"] >= accept["text/html"]


def register_error_handlers(app: Flask) -> None:
    """在应用上注册 AppError 与 HTTPException 的统一处理器。"""

    @app.errorhandler(AppError)
    def _handle_app_error(exc: AppError):
        observability.log_event(
            observability.EVENT_REQUEST_FAILED,
            # 4xx 是调用方的问题（WARNING 就够），5xx 才是服务端故障。
            level="ERROR" if exc.http_status >= 500 else "WARNING",
            # 只记 path，不记 query / body（可能带目标列表）。
            path=request.path,
            method=request.method,
            status=exc.http_status,
            error_code=exc.code,
            error_message=exc.message,
        )
        return jsonify(exc.to_dict()), exc.http_status

    @app.errorhandler(HTTPException)
    def _handle_http_error(exc: HTTPException):
        if not wants_json():
            return exc
        status = exc.code or 500
        code = _STATUS_TO_CODE.get(status, ErrorCode.UNKNOWN_ERROR if status >= 500 else ErrorCode.BAD_REQUEST)
        observability.log_event(
            observability.EVENT_REQUEST_FAILED,
            level="ERROR" if status >= 500 else "WARNING",
            path=request.path,
            method=request.method,
            status=status,
            error_code=code,
            error_message=exc.description or exc.name,
        )
        return (
            jsonify(
                {
                    "ok": False,
                    "error_code": code,
                    "error_message": exc.description or exc.name,
                }
            ),
            status,
        )

    @app.errorhandler(Exception)
    def _handle_unexpected_error(exc: Exception):  # pragma: no cover - 兜底分支
        app.logger.exception("未处理异常: %s", exc)
        observability.log_event(
            observability.EVENT_UNHANDLED_EXCEPTION,
            level="ERROR",
            path=request.path,
            method=request.method,
            status=500,
            error_code=ErrorCode.UNKNOWN_ERROR,
            # 只记异常类型与 repr（值会被脱敏），完整栈在 app.logger 的 traceback 里。
            exception=type(exc).__name__,
            detail=repr(exc),
        )
        return (
            jsonify(
                {
                    "ok": False,
                    "error_code": ErrorCode.UNKNOWN_ERROR,
                    "error_message": "服务内部错误，详情见本机控制台日志",
                }
            ),
            500,
        )
