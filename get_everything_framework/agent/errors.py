class LLMError(Exception):
    """模型调用基础异常"""


class LLMConfigError(LLMError):
    """模型配置错误"""


class LLMAuthError(LLMError):
    """认证失败，例如 API Key 错误"""


class LLMRateLimitError(LLMError):
    """模型服务限流"""


class LLMTimeoutError(LLMError):
    """模型请求超时"""


class LLMConnectionError(LLMError):
    """模型服务连接失败"""


class LLMResponseError(LLMError):
    """模型返回结构异常"""


class LLMServerError(LLMError):
    """模型服务端异常"""
