"""结构化日志与关联 ID（方案第 19 节 P1：Observability）。

方案第 19 节要求「逐步加入」四个关联字段并让日志结构化：

```text
request_id
job_id
step_id
worker_id
```

建议的事件形状（同一节的示例）::

    {"level":"INFO","event":"job_step_finished","job_id":"job_xxx",
     "step_id":"step_xxx","tool":"httpx","status":"succeeded","duration_ms":1200}

本模块是该要求的**唯一出口**，提供三件事：

1. :func:`log_event` —— 一行一个 JSON 事件，写进 stdlib ``logging`` 的
   ``gef`` logger（不引入 structlog / loguru 等新依赖）；
2. :func:`bind` / :func:`set_context` —— 用 ``contextvars`` 把
   ``request_id`` / ``job_id`` / ``step_id`` / ``worker_id`` 绑到当前执行流上，
   于是「worker → 任务 → 步骤」里任何一层打日志都自动带全上下文，
   不需要逐层透传参数（contextvar 在线程内独立，waitress 的多线程模型下
   每个请求互不串号）；
3. 默认脱敏 —— 所有字段值先过 :func:`core.runner_result.scrub_text`，
   密钥、``user:password@``、裸长 token 一律打码（方案第 19 节明令禁止
   ``print(f"api_key={key}")`` 这类写法）。

三条刻意的设计约束：

* ``*_id`` 结尾的字段按**标识符原样**记录（只截断长度）。它们正是关联日志的
  钥匙，若被「裸长 token 兜底」规则打码（``job_9d8a…`` 恰好长 36 个字符），
  结构化日志就自废武功了；自由文本字段里的 ID 形状也会先挖出来占位再还原。
* 字段名命中 :data:`_SENSITIVE_KEY_RE`（api_key / token / secret / password /
  authorization / cookie / credential）时**只记占位符**，值绝不落盘。
* 容器（list/dict）最多记 :data:`MAX_CONTAINER_ITEMS` 项 —— 方案第 19 节明确
  「不要记录完整目标列表到公共日志」，完整内容请落 artifact。
"""

from __future__ import annotations

import json
import logging
import re
import sys
import uuid
from contextlib import contextmanager
from contextvars import ContextVar, Token
from datetime import datetime, timezone
from typing import Any, Iterator, Optional

from core.runner_result import REDACTED, scrub_text
# 日志器名：configure_logging() 只在这个 logger 上挂 handler，不碰 root。
LOGGER_NAME = "gef"

# 关联 ID 的请求头：入站带了就沿用（便于跨进程追踪），没带就生成。
REQUEST_ID_HEADER = "X-Request-Id"

# 只接受「看起来像追踪 ID」的入站值：既防止原样回显任意长/含空白的头，
# 也保证写进日志的字段一定是安全的单行短串。
REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{8,64}$")

# 单字段最大长度：日志要能一眼看完；整份 stdout 这种长内容请落 artifact。
MAX_FIELD_CHARS = 500

# 容器最多记录这么多项（方案第 19 节：不把完整目标列表写进公共日志）。
MAX_CONTAINER_ITEMS = 20

DEFAULT_LEVEL = "INFO"
DEFAULT_FORMAT = "json"
SUPPORTED_FORMATS = ("json", "text")

# ── 事件名（跨层共用，避免各处手写字符串漂移） ──────────────

EVENT_HTTP_REQUEST = "http_request_finished"
EVENT_REQUEST_FAILED = "request_failed"
EVENT_UNHANDLED_EXCEPTION = "unhandled_exception"
EVENT_JOB_CREATED = "job_created"
EVENT_JOB_STARTED = "job_started"
EVENT_JOB_FINISHED = "job_finished"
EVENT_JOB_STEP_FINISHED = "job_step_finished"
EVENT_AGENT_PLAN_STEP = "agent_plan_step"
EVENT_WORKER_STARTED = "worker_started"
EVENT_WORKER_RECOVERED_JOB = "worker_recovered_job"
EVENT_WORKER_CLAIMED_JOB = "worker_claimed_job"
EVENT_WORKER_JOB_FINISHED = "worker_job_finished"
EVENT_WORKER_JOB_EXCEPTION = "worker_job_exception"
EVENT_WORKER_IDLE_EXIT = "worker_idle_exit"
EVENT_WORKER_SIGNAL = "worker_signal_received"
EVENT_WORKER_SHUTDOWN = "worker_shutdown"
EVENT_WORKER_MESSAGE = "worker_message"

# ── 字段与值的清洗规则 ────────────────────────────────────

# 命中即「值一律不记」。注意这条规则优先于 ``*_id`` 规则。
_SENSITIVE_KEY_RE = re.compile(
    r"(?i)(api[_-]?key|apikey|token|secret|password|passwd|credential|authorization|cookie)"
)

# ``*_id`` 字段是关联日志的钥匙，按标识符原样记录（只截断长度）。
_ID_KEY_RE = re.compile(r"(?i)(^|_)id$")

# 自由文本里出现「本项目自己的 ID 形状」时先挖出来占位，避免被
# ``_LONG_TOKEN_RE``（≥20 个连续 [A-Za-z0-9_-]）当成裸 token 打码。
_ID_IN_TEXT_RE = re.compile(r"\b(?:job|step|run|asset|obs|art|exp|scope|evt|req|wkr)_[0-9a-f]{6,}\b")

_LEVELS = {
    "CRITICAL": logging.CRITICAL,
    "ERROR": logging.ERROR,
    "WARNING": logging.WARNING,
    "INFO": logging.INFO,
    "DEBUG": logging.DEBUG,
}

# ── contextvars ──────────────────────────────────────────

_CONTEXT_VARS: dict[str, ContextVar[Optional[str]]] = {
    "request_id": ContextVar("gef_request_id", default=None),
    "job_id": ContextVar("gef_job_id", default=None),
    "step_id": ContextVar("gef_step_id", default=None),
    "worker_id": ContextVar("gef_worker_id", default=None),
}
_CONTEXT_ORDER = ("request_id", "job_id", "step_id", "worker_id")

# ``ContextVar.set()`` 的返回类型，用于 :func:`set_context` / :func:`reset_context`。
ContextToken = Token[Optional[str]]

_FORMAT = DEFAULT_FORMAT

# logger 只在本模块配置。默认挂 NullHandler：未调 configure_logging() 时
# （库用法、单元测试）安静不输出，也不会触发 logging 的 lastResort 兜底打印；
# 同时保留 ``propagate=True``，这样 pytest 的 ``caplog`` / 使用者自己的 root
# handler 仍能捕获事件。configure_logging() 会挂上自己的 handler 并把
# propagate 关掉，保证生产环境里每条事件只输出一次。
logger = logging.getLogger(LOGGER_NAME)
logger.setLevel(logging.DEBUG)
if not any(isinstance(handler, logging.NullHandler) for handler in logger.handlers):
    logger.addHandler(logging.NullHandler())


def utc_now() -> str:
    """当前 UTC 时间（ISO-8601，秒精度足够日志排序）。"""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_request_id() -> str:
    """生成一个新的 ``request_id``（``req_<uuid4hex>``）。"""
    return f"req_{uuid.uuid4().hex}"


def accept_request_id(value: Any) -> str | None:
    """校验入站 ``X-Request-Id``：安全且长度合理才沿用，否则返回 ``None``。"""
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    return candidate if REQUEST_ID_PATTERN.match(candidate) else None


def set_context(name: str, value: Any) -> ContextToken | None:
    """把某个关联字段绑到当前执行流，返回可用于还原的 token。

    名字不在 :data:`_CONTEXT_VARS` 中或值为 ``None`` 时不做任何事（返回 ``None``）。
    """
    var = _CONTEXT_VARS.get(name)
    if var is None or value is None:
        return None
    return var.set(str(value))


def reset_context(name: str, token: ContextToken | None) -> None:
    """还原 :func:`set_context` 绑定的值（必须与同名 set 成对出现）。"""
    var = _CONTEXT_VARS.get(name)
    if var is None or token is None:
        return
    var.reset(token)


def set_request_id(value: Any) -> ContextToken | None:
    """绑定 ``request_id``（Web 层用）。"""
    return set_context("request_id", value)


def reset_request_id(token: ContextToken | None) -> None:
    """还原 ``request_id``。"""
    reset_context("request_id", token)


def current_context() -> dict[str, str]:
    """当前执行流上已绑定的关联字段（缺的字段不会出现在结果里）。"""
    context: dict[str, str] = {}
    for name in _CONTEXT_ORDER:
        value = _CONTEXT_VARS[name].get()
        if value:
            context[name] = value
    return context


@contextmanager
def bind(**fields: Any) -> Iterator[None]:
    """在 ``with`` 块内绑定若干关联字段，退出时精确还原。

    用法::

        with observability.bind(worker_id="w-1", job_id=job_id):
            execute_job(job_id)   # 这一层里打的所有事件都自动带 worker_id / job_id
    """
    tokens: list[tuple[str, ContextToken]] = []
    try:
        for name, value in fields.items():
            token = set_context(name, value)
            if token is not None:
                tokens.append((name, token))
        yield
    finally:
        for name, token in reversed(tokens):
            _CONTEXT_VARS[name].reset(token)


# ── 字段清洗 ─────────────────────────────────────────────


def _cap(text: str) -> str:
    """按 :data:`MAX_FIELD_CHARS` 截断（用 ``...`` 明确表示被截断）。"""
    if len(text) <= MAX_FIELD_CHARS:
        return text
    return text[: MAX_FIELD_CHARS - 3] + "..."


def _scrub_free_text(text: str) -> str:
    """脱敏自由文本，但保留本项目自己的 ID 形状。

    底层复用 :func:`core.runner_result.scrub_text`（单一脱敏出口），因此连
    **命令行风格**的 ``-api-key VALUE`` 规则也一并对自由文本生效。副作用是
    纯散文里出现 ``X-Local-Token 请求头`` 这类写法时，紧随其后的词会被误打码。
    这是刻意的**失败即关闭**取舍：日志宁可多打码，也不能漏一个真实密钥 ——
    完整、未脱敏的原文仍可在 HTTP 响应与 ``audit_events`` 里看到。
    """
    placeholders: list[str] = []

    def _hide(match: re.Match[str]) -> str:
        placeholders.append(match.group(0))
        return f"\x00{len(placeholders) - 1}\x00"

    hidden = _ID_IN_TEXT_RE.sub(_hide, text)
    scrubbed = scrub_text(hidden)
    for index, original in enumerate(placeholders):
        scrubbed = scrubbed.replace(f"\x00{index}\x00", original)
    return scrubbed


def _clean_value(value: Any, *, identifier: bool = False) -> Any:
    """把一个任意值清洗成可安全写进日志、且可 JSON 序列化的形状。"""
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        return _cap(value if identifier else _scrub_free_text(value))
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_clean_value(item) for item in list(value)[:MAX_CONTAINER_ITEMS]]
    if isinstance(value, dict):
        cleaned: dict[str, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= MAX_CONTAINER_ITEMS:
                break
            cleaned[str(key)] = _clean_field(str(key), item)
        return cleaned
    return _cap(_scrub_free_text(repr(value)))


def _clean_field(key: str, value: Any) -> Any:
    """按字段名决定清洗策略（敏感字段只记占位符）。"""
    if _SENSITIVE_KEY_RE.search(key):
        return REDACTED
    return _clean_value(value, identifier=bool(_ID_KEY_RE.search(key)))


def _resolve_level(level: Any) -> tuple[int, str]:
    """把 ``"info"`` / ``logging.INFO`` / 未知值统一成 ``(数值, 大写名)``。"""
    if isinstance(level, int):
        name = logging.getLevelName(level)
        if isinstance(name, str) and name in _LEVELS:
            return level, name
        return logging.INFO, "INFO"
    name = str(level or DEFAULT_LEVEL).strip().upper()
    return _LEVELS.get(name, logging.INFO), (name if name in _LEVELS else "INFO")


def format_event(payload: dict[str, Any], fmt: str | None = None) -> str:
    """把一个事件渲染成单行字符串（``json`` 或 ``text``）。"""
    resolved = (fmt or _FORMAT or DEFAULT_FORMAT).strip().lower()
    if resolved not in SUPPORTED_FORMATS:
        resolved = DEFAULT_FORMAT
    if resolved == "text":
        parts = []
        for key, value in payload.items():
            rendered = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
            parts.append(f"{key}={rendered}")
        return " ".join(parts)
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)


def log_event(event: str, *, level: Any = "INFO", **fields: Any) -> dict[str, Any]:
    """记一条结构化事件，返回最终写入的 payload（便于测试与断言）。

    Args:
        event: 事件名，例如 :data:`EVENT_JOB_STEP_FINISHED`。
        level: ``"INFO"`` / ``"WARNING"`` / ``"ERROR"`` …（未知值退化为 INFO）。
        **fields: 业务字段。字段名的清洗策略见模块文档；当前关联字段
            （``request_id`` / ``job_id`` / ``step_id`` / ``worker_id``）
            会自动并入，显式传入同名字段时以显式值为准。

    Returns:
        dict: ``{"ts", "level", "event", ...上下文, ...业务字段}``。
    """
    level_no, level_name = _resolve_level(level)
    payload: dict[str, Any] = {
        "ts": utc_now(),
        "level": level_name,
        "event": str(event) if event is not None and str(event).strip() else "unspecified",
    }
    payload.update(current_context())
    for key, value in fields.items():
        payload[str(key)] = _clean_field(str(key), value)
    logger.log(level_no, format_event(payload))
    return payload


def configure_logging(level: Any = None, fmt: str | None = None, stream: Any = None) -> logging.Logger:
    """给 ``gef`` logger 装上（或重装）一个输出到 ``stderr`` 的单行 handler。

    幂等：重复调用只会保留一个由本函数安装的 handler，不会重复输出。
    默认级别 / 格式取自 :class:`config.Config` 的 ``LOG_LEVEL`` / ``LOG_FORMAT``
    （配置不可用时退化为 ``INFO`` / ``json``）。
    """
    global _FORMAT

    default_level, default_format = _defaults()
    level_no, _ = _resolve_level(level if level is not None else default_level)
    resolved_format = str(fmt if fmt is not None else default_format).strip().lower()
    if resolved_format not in SUPPORTED_FORMATS:
        resolved_format = DEFAULT_FORMAT

    for existing in list(logger.handlers):
        if getattr(existing, "_gef_stream", False):
            logger.removeHandler(existing)
            existing.close()

    handler = logging.StreamHandler(stream if stream is not None else sys.stderr)
    handler.setFormatter(logging.Formatter("%(message)s"))
    handler._gef_stream = True  # type: ignore[attr-defined]
    logger.addHandler(handler)
    logger.setLevel(level_no)
    # 已经有一个专属 handler 了，不要再经 root 输出一遍。
    logger.propagate = False
    _FORMAT = resolved_format
    return logger


def _defaults() -> tuple[str, str]:
    """从 ``config.Config`` 读默认级别与格式（读不到就用模块常量）。"""
    try:
        from config import Config

        return getattr(Config, "LOG_LEVEL", DEFAULT_LEVEL), getattr(Config, "LOG_FORMAT", DEFAULT_FORMAT)
    except Exception:  # noqa: BLE001 - 配置不可用时不能让日志本身炸掉
        return DEFAULT_LEVEL, DEFAULT_FORMAT
