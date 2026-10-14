"""统一错误模型。

设计目标（方案第 6.2 节）：失败不能只返回空列表，必须带上机器可判定的
``error_code`` 与人类可读的 ``error_message``，让「工具不存在」「超时」
「空结果」在 API 层可以被区分。

本模块只定义错误码与异常基类，不依赖 Flask，方便在 runner、worker、
CLI 中复用。
"""

from __future__ import annotations


class ErrorCode:
    """统一错误码常量（字符串值既是 API 输出，也是日志字段）。"""

    TOOL_NOT_FOUND = "tool_not_found"
    PERMISSION_DENIED = "permission_denied"
    INVALID_TARGET = "invalid_target"
    SCOPE_VIOLATION = "scope_violation"
    TIMEOUT = "timeout"
    PARSE_ERROR = "parse_error"
    NETWORK_ERROR = "network_error"
    RATE_LIMITED = "rate_limited"
    PARTIAL_SUCCESS = "partial_success"
    UNKNOWN_ERROR = "unknown_error"

    # 非工具类错误码：本机联调版在 API 层直接使用。
    NO_RESULTS = "no_results"
    # **前置数据缺失**：这一步压根没有执行（进程未启动），因为它的上游没有
    # 产出任何可用的目标。与 ``no_results``（工具跑通了、但确实没发现东西）
    # 必须分开：前者要看上游，后者要看数据源/目标本身，排查方向完全相反。
    NO_INPUT = "no_input"
    # 配置指向的外部资源缺失（当前只有一处：字典文件不存在）。
    # 语义上既不是「工具没装」（tool_not_found）也不是「目标非法」
    # （invalid_target），而是**使用者的配置问题**，必须能单独报出来。
    CONFIG_ERROR = "config_error"
    # **外部数据源受阻**：查询渠道本身要求先通过「安全验证」（人机校验 /
    # 登录态失效），于是这次查询**没有完成**。
    #
    # 为什么不能复用既有错误码：
    # * ``no_results`` 是「跑通了、确实没有」——把它当成零结果就是本码要修
    #   的那个 bug（页面上会显示成「执行成功但零结果」）；
    # * ``rate_limited`` 是「被限流、等一会儿再来」，而安全验证**再等也不会
    #   自己好**，必须由人去完成验证，混用会把使用者引向无限重试；
    # * ``network_error`` 不成立（网络是通的，服务端明确回了校验要求）；
    # * ``config_error`` 是「本机配置指向的文件缺失」，而这里可能压根不是
    #   配置问题（有 Cookie 也会被风控拦）。
    SOURCE_BLOCKED = "source_blocked"
    UNAUTHENTICATED = "unauthenticated"
    BAD_REQUEST = "bad_request"
    NOT_FOUND = "not_found"
    # 任务层：worker 中断，需要人工 retry（区别于工具自身的失败）。
    INTERRUPTED = "interrupted"

    ALL = (
        TOOL_NOT_FOUND,
        PERMISSION_DENIED,
        INVALID_TARGET,
        SCOPE_VIOLATION,
        TIMEOUT,
        PARSE_ERROR,
        NETWORK_ERROR,
        RATE_LIMITED,
        PARTIAL_SUCCESS,
        UNKNOWN_ERROR,
        NO_RESULTS,
        NO_INPUT,
        CONFIG_ERROR,
        SOURCE_BLOCKED,
        UNAUTHENTICATED,
        BAD_REQUEST,
        NOT_FOUND,
        INTERRUPTED,
    )


class AppError(Exception):
    """所有业务异常的基类。

    Attributes:
        code: ``ErrorCode`` 中的错误码。
        message: 面向使用者的中文说明（可直接进 JSON 响应）。
        http_status: 建议的 HTTP 状态码。
        details: 附加结构化信息（如冲突的目标、工具名），默认空字典。
    """

    code = ErrorCode.UNKNOWN_ERROR
    http_status = 500

    def __init__(self, message: str, *, code: str | None = None, details: dict | None = None):
        super().__init__(message)
        self.message = message
        if code:
            self.code = code
        self.details = details or {}

    def to_dict(self) -> dict:
        """转换为 API 响应体片段。"""
        payload = {
            "ok": False,
            "error_code": self.code,
            "error_message": self.message,
        }
        if self.details:
            payload["details"] = self.details
        return payload


class BadRequestError(AppError):
    """请求参数缺失或格式非法。"""

    code = ErrorCode.BAD_REQUEST
    http_status = 400


class InvalidTargetError(AppError):
    """目标本身不是合法的域名或 IP/CIDR。"""

    code = ErrorCode.INVALID_TARGET
    http_status = 400


class ScopeViolationError(AppError):
    """目标落在授权范围之外，或任务未关联任何 Scope。"""

    code = ErrorCode.SCOPE_VIOLATION
    http_status = 403


class ToolNotFoundError(AppError):
    """请求的工具未注册或本机不可用。"""

    code = ErrorCode.TOOL_NOT_FOUND
    http_status = 400


class UnauthenticatedError(AppError):
    """缺少本地管理员会话或 Token。"""

    code = ErrorCode.UNAUTHENTICATED
    http_status = 401


class NotFoundError(AppError):
    """请求的实体（job / scope / upload …）不存在。"""

    code = ErrorCode.NOT_FOUND
    http_status = 404
