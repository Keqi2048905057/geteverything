"""统一工具结果模型（M4，方案第 6.2 节）。

**要解决的问题**：原实现里 runner 失败时只 ``return []``，调用方无法区分

* 工具没装（``tool_not_found``）；
* 工具超时（``timeout``）；
* 工具跑了但输出解析不动（``parse_error``）；
* 工具跑通、确实什么都没发现（``no_results``）。

这四种情况在旧代码里全都表现为「空列表」，是本项目最贵的坑
（``docs/CODEBASE_MAP.md`` §7.1 第 1 条）。

**设计约束**：

* 本模块不依赖 Flask、不依赖 sqlite，runner / worker / CLI 都能直接用；
* 不执行任何子进程、不读配置、不落盘；纯数据模型 + 少量工具函数；
* ``command_preview`` 必须脱敏：方案第 8.2 节明确禁止把 API Key 写进命令预览。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from core.errors import ErrorCode

# 解析器版本：解析逻辑改了就必须改这里，写进 observation 便于追溯
# 「这条数据是哪版解析器产出的」（方案 M4 交付项）。
PARSER_VERSION = "1.0"

# RunnerResult.status 的取值（与 core.mock 的场景状态对齐）。
STATUS_SUCCESS = "success"
STATUS_PARTIAL = "partial"
STATUS_FAILED = "failed"
STATUS_TIMEOUT = "timeout"

STATUSES = (STATUS_SUCCESS, STATUS_PARTIAL, STATUS_FAILED, STATUS_TIMEOUT)

# 命令行里出现这些参数名时，紧跟其后的值必须被打码。
_SECRET_FLAG_RE = re.compile(
    r"(?i)(--?(?:api[-_]?key|token|secret|password|passwd|pwd|auth|cookie|session|credential)"
    r"(?:=|:|\s+))([^\s'\"]+)"
)

# ``Key=Value`` 形态的凭据（HTTP header、环境变量式参数）。
_SECRET_PAIR_RE = re.compile(
    r"(?i)\b((?:x-)?(?:api[-_]?key|token|secret|password|passwd|authorization|cookie)"
    r"\s*[=:]\s*)([^\s,;'\"]+)"
)

# URL 里的用户信息 ``https://user:pass@host``。
_URL_CREDENTIAL_RE = re.compile(r"(?i)(\b[a-z][a-z0-9+.-]*://[^/\s:@]+):([^@/\s]+)@")

# 常见 Key 形态兜底：长度较长的裸 token。
# 用前后视断言排除**路径片段**——否则 `results\get_everything_framework\x.txt`
# 这种长目录名会被当成密钥打码，把有用的命令预览毁掉。
_LONG_TOKEN_RE = re.compile(r"(?<![\w./\\-])[A-Za-z0-9_\-]{20,}(?![\w./\\-])")

# 路径片段兜底：``E:\a\long_dir_name\out.txt`` 这类长目录名同样不能打码。
# 上面的 lookaround 已覆盖大多数情况，这里额外保护带盘符的 Windows 路径尾巴。

REDACTED = "***"

# 命令预览的最大长度：命令很长时只保留头部，避免把整份目标列表塞进数据库。
MAX_COMMAND_PREVIEW = 300


def scrub_command(cmd: list[str] | tuple[str, ...] | str) -> str:
    """把命令行渲染成**可安全入库、可安全回显**的预览字符串。

    做三件事：

    1. 参数名敏感（``-api-key`` / ``--token`` / ``Authorization:``）时打码其值；
    2. 打码 URL 中的 ``user:password@`` 段；
    3. 裸的长 token 串打码（兜底，防止工具把 Key 放在位置参数里）。

    Args:
        cmd: 参数列表或已经拼好的命令字符串。

    Returns:
        str: 脱敏后的单行预览，最长 :data:`MAX_COMMAND_PREVIEW` 个字符。
    """
    if isinstance(cmd, str):
        joined = cmd
    else:
        # 含空格的参数加引号，让预览与真实 argv 语义一致（但仍是纯展示）。
        parts = []
        for item in cmd:
            text = str(item)
            parts.append(f'"{text}"' if (" " in text or "\t" in text) else text)
        joined = " ".join(parts)

    joined = _URL_CREDENTIAL_RE.sub(rf"\1:{REDACTED}@", joined)
    joined = _SECRET_FLAG_RE.sub(rf"\1{REDACTED}", joined)
    joined = _SECRET_PAIR_RE.sub(rf"\1{REDACTED}", joined)
    joined = _LONG_TOKEN_RE.sub(REDACTED, joined)

    if len(joined) > MAX_COMMAND_PREVIEW:
        joined = joined[: MAX_COMMAND_PREVIEW - 3] + "..."
    return joined


@dataclass
class Observation:
    """一条结构化观测结果：资产值 + 该工具的附加字段。

    ``httpx`` 这类工具一次响应能产出多个字段（URL / 状态码 / 标题 /
    Web Server / 技术栈 / CDN），因此不能只把 URL 存成字符串 ——
    这些字段就是 M5 资产页要展示的内容。
    """

    category: str
    value: str
    data: dict[str, Any] = field(default_factory=dict)
    parser_version: str = PARSER_VERSION
    source_tool: str | None = None

    def to_dict(self) -> dict:
        payload: dict[str, Any] = {
            "category": self.category,
            "value": self.value,
            "data": dict(self.data),
            "parser_version": self.parser_version,
        }
        if self.source_tool:
            payload["source_tool"] = self.source_tool
        return payload

    @classmethod
    def from_dict(cls, raw: dict) -> "Observation":
        return cls(
            category=str(raw.get("category") or ""),
            value=str(raw.get("value") or ""),
            data=dict(raw.get("data") or {}),
            parser_version=str(raw.get("parser_version") or PARSER_VERSION),
            source_tool=raw.get("source_tool"),
        )


@dataclass
class ToolHealth:
    """工具可用性（M4 起由 runner 自己回答，不再靠猜）。"""

    name: str
    available: bool
    path: str | None = None
    version: str | None = None
    error_code: str | None = None
    message: str | None = None

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "available": self.available,
            "path": self.path,
            "version": self.version,
            "error_code": self.error_code,
            "message": self.message,
        }


class RunnerInputError(Exception):
    """runner 在**启动子进程之前**就判定无法执行。

    典型场景：``httpx`` 没有任何候选目标可探测。这类情况与「工具跑完但
    零结果」在语义上不同，必须能表达成结构化结果而不是裸异常，否则会被
    ``run()`` 兜底成 ``unknown_error``，把「缺少前置数据」误报成工具故障。
    """

    def __init__(self, error_code: str, message: str, *, status: str = STATUS_FAILED):
        super().__init__(message)
        self.error_code = error_code
        self.message = message
        self.status = status


@dataclass
class RunnerResult:
    """一次工具执行的完整结果（方案第 6.2 节的 JSON 形状 + 本机联调需要的字段）。

    Attributes:
        status: ``success`` / ``partial`` / ``failed`` / ``timeout``。
        data: 结构化观测列表。
        error_code: ``ErrorCode`` 中的错误码；**跑通但零结果时是 ``no_results``**，
            这是与「失败」区分开的关键（方案 M4 验收项）。
        error_message: 面向使用者的中文说明（stderr 原文不进这里，见 §8.2）。
        exit_code: 子进程退出码；工具没起来时为 ``None``。
        duration_ms: 执行耗时（毫秒）。
        command_preview: 已脱敏的命令行预览。
        raw_artifact_id: 原始 stdout/stderr 产物的 ID（存 ``artifacts`` 表）。
        parser_version: 产出 ``data`` 的解析器版本。
        stderr_preview: stderr 的截断预览，**只用于本地排查**，不进 API 出参。
    """

    status: str = STATUS_SUCCESS
    data: list[Observation] = field(default_factory=list)
    error_code: str | None = None
    error_message: str | None = None
    exit_code: int | None = None
    duration_ms: int | None = None
    command_preview: str | None = None
    raw_artifact_id: str | None = None
    parser_version: str = PARSER_VERSION
    stderr_preview: str | None = None
    tool_name: str | None = None
    target: str | None = None

    def __post_init__(self) -> None:
        if self.status not in STATUSES:
            raise ValueError(f"未知的 RunnerResult.status: {self.status!r}")

    # ── 便捷构造 ───────────────────────────────────────────

    @classmethod
    def ok(
        cls,
        data: list[Observation] | None = None,
        **kwargs,
    ) -> "RunnerResult":
        """执行成功。``data`` 为空时自动标 ``no_results``。"""
        items = list(data or [])
        if not items:
            return cls(
                status=STATUS_SUCCESS,
                data=[],
                error_code=ErrorCode.NO_RESULTS,
                error_message="工具执行成功但未发现任何结果",
                **kwargs,
            )
        return cls(status=STATUS_SUCCESS, data=items, **kwargs)

    @classmethod
    def failure(
        cls,
        error_code: str,
        error_message: str,
        *,
        status: str = STATUS_FAILED,
        **kwargs,
    ) -> "RunnerResult":
        """执行失败（含超时）。"""
        return cls(
            status=status,
            data=[],
            error_code=error_code,
            error_message=error_message,
            **kwargs,
        )

    # ── 派生属性 ───────────────────────────────────────────

    @property
    def found_count(self) -> int:
        return len(self.data)

    @property
    def values(self) -> list[str]:
        """只要值列表（旧调用方与 ``job_steps.results_json`` 仍按值存储）。"""
        return [item.value for item in self.data]

    @property
    def is_failure(self) -> bool:
        """真正的失败（超时或带非 ``no_results`` 错误码）。"""
        if self.status == STATUS_TIMEOUT:
            return True
        return self.error_code not in (None, ErrorCode.NO_RESULTS)

    # ── 序列化 ─────────────────────────────────────────────

    def to_dict(self) -> dict:
        """方案第 6.2 节要求的 JSON 形状。"""
        payload: dict[str, Any] = {
            "status": self.status,
            "data": [item.to_dict() for item in self.data],
            "found_count": self.found_count,
            "error_code": self.error_code,
            "error_message": self.error_message,
            "exit_code": self.exit_code,
            "duration_ms": self.duration_ms,
            "command_preview": self.command_preview,
            "raw_artifact_id": self.raw_artifact_id,
            "parser_version": self.parser_version,
        }
        if self.tool_name:
            payload["tool_name"] = self.tool_name
        if self.target:
            payload["target"] = self.target
        return payload

    def to_step_outcome(self) -> dict:
        """转成 ``jobs/executor.py`` 写入 ``job_steps`` 的形状。

        ``job_steps`` 的列是 M3 定下的（``found_count`` / ``results_json`` /
        ``error_code`` / ``exit_code`` …），这里保持兼容，同时把新增的
        ``artifact_id`` 与结构化 ``data`` 一并带出去。
        """
        step_status = {
            STATUS_SUCCESS: "succeeded",
            STATUS_PARTIAL: "succeeded",
            STATUS_FAILED: "failed",
            STATUS_TIMEOUT: "timeout",
        }[self.status]
        return {
            "step_status": step_status,
            "found_count": self.found_count,
            "results": self.values,
            "observations": [item.to_dict() for item in self.data],
            "error_code": self.error_code,
            "error_message": self.error_message,
            "exit_code": self.exit_code,
            "artifact_id": self.raw_artifact_id,
            "duration_ms": self.duration_ms,
            "command_preview": self.command_preview,
            "parser_version": self.parser_version,
        }


def result_from_exception(
    exc: BaseException,
    *,
    tool_name: str | None = None,
    target: str | None = None,
    command_preview: str | None = None,
) -> RunnerResult:
    """把 runner 抛出的异常统一翻译成 :class:`RunnerResult`。

    这是「禁止失败返回 ``[]``」的兜底实现：任何未预料异常都不会变成
    一个看起来正常的空结果。
    """
    if isinstance(exc, RunnerInputError):
        return RunnerResult.failure(
            exc.error_code,
            exc.message,
            status=exc.status,
            tool_name=tool_name,
            target=target,
            command_preview=command_preview,
        )
    if isinstance(exc, SystemExit):
        code = exc.code if isinstance(exc.code, int) else 1
        return RunnerResult.failure(
            ErrorCode.UNKNOWN_ERROR,
            f"工具非正常退出（SystemExit {code}）",
            exit_code=code,
            tool_name=tool_name,
            target=target,
            command_preview=command_preview,
        )
    if isinstance(exc, FileNotFoundError):
        return RunnerResult.failure(
            ErrorCode.TOOL_NOT_FOUND,
            str(exc) or f"未找到可执行文件: {tool_name}",
            tool_name=tool_name,
            target=target,
            command_preview=command_preview,
        )
    if isinstance(exc, PermissionError):
        return RunnerResult.failure(
            ErrorCode.PERMISSION_DENIED,
            str(exc) or "权限不足，无法执行该工具",
            tool_name=tool_name,
            target=target,
            command_preview=command_preview,
        )
    if isinstance(exc, TimeoutError):
        return RunnerResult.failure(
            ErrorCode.TIMEOUT,
            str(exc) or "执行超时",
            status=STATUS_TIMEOUT,
            tool_name=tool_name,
            target=target,
            command_preview=command_preview,
        )
    return RunnerResult.failure(
        ErrorCode.UNKNOWN_ERROR,
        f"{type(exc).__name__}: {exc}",
        tool_name=tool_name,
        target=target,
        command_preview=command_preview,
    )


def preview_text(value: str | None, limit: int = 2000) -> str | None:
    """截断一段文本用于本地排查（stderr 预览），并做脱敏。"""
    if not value:
        return None
    text = scrub_command(value) if len(value) > 0 else value
    if len(text) > limit:
        text = text[: limit - 3] + "..."
    return text
