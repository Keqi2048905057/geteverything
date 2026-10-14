"""扫描模式与真实扫描开关。

方案第 2.3 节的硬性约束：

* 默认使用 mock 工具模式完成自动化测试（第 9 条）；
* 真实外部工具执行必须显式开启（第 8 条）。

本模块提供唯一的判定入口，避免各处在代码里散落 ``os.getenv("...")``：

* :func:`real_scan_enabled` —— 读环境开关 ``GEF_ALLOW_REAL_SCAN``；
* :func:`open_scan_enabled` —— 读「开放模式」开关 ``GEF_OPEN_SCAN_MODE``；
* :func:`resolve_mode` —— 把请求里的 ``mode`` 解析成 ``mock`` / ``real``，
  未显式开启开关时拒绝 ``real``；
* :func:`is_local_only_target` —— 判断目标是否只指向本机/保留域名，
  供 Scope 创建时的提示与测试使用。

## 开放模式（``GEF_OPEN_SCAN_MODE``，**源码默认关闭**）

历史行为要求「每一个任务都必须挂在一个显式的授权 Scope 上」，否则
:func:`core.policy.require_scope` 直接 400。开放模式允许**取消这条授权业务
要求**：扫描中心不再要求先建授权项目与范围，目标也不再逐条比对白名单。

语义是**「允许不挂 Scope 直接扫描」**：

* 缺省（未设置）= **关闭**：保持历史行为 —— 没有授权范围时不允许创建扫描任务，
  ``scope_id`` 缺失即 400，错误信息明确点名
  ``GEF_OPEN_SCAN_MODE=true``（见 :func:`core.policy.require_scope`）；
* 显式设为真值（``1`` / ``true`` / ``yes`` / ``on`` / ``y``）= 开启：
  ``scope_id`` 为空时由 :func:`core.scope.open_scope` 生成一个开放范围，
  任务照常创建。

**口径（不要改回去）**：源码默认必须是 ``false``。本次是在本机测试环境的
``.env`` 里主动打开开放扫描能力，不代表公开仓库下载后默认进入开放公网扫描
模式。任何**拼错的值**（例如 ``ture``）一律按「关闭」处理 —— 失败关闭，
不静默放行。

这条开关**只**回答「要不要必须有 Scope」。它不放松
:data:`REAL_SCAN_ENV`（真实外部扫描总开关），也不放松
``excluded_domains``（排除优先）与 :func:`core.policy.validate_resolved_address`
（解析到内网地址一律拒绝）—— 「不要求授权」不等于「不做任何安全检查」。
"""

from __future__ import annotations

import ipaddress
import os

from core.errors import BadRequestError, ScopeViolationError

# 环境变量：只有显式设为真值时才允许真实外部扫描。
REAL_SCAN_ENV = "GEF_ALLOW_REAL_SCAN"

# 环境变量：允许「不挂授权范围」直接创建任务。
# **源码默认关闭**：只有显式设为真值才开启。公开仓库下载后默认仍需授权范围，
# 本机测试环境在 .env 里写 ``GEF_OPEN_SCAN_MODE=true``。
OPEN_SCAN_ENV = "GEF_OPEN_SCAN_MODE"

MODE_MOCK = "mock"
MODE_REAL = "real"
SUPPORTED_MODES = (MODE_MOCK, MODE_REAL)

# RFC 6761 / 6762 保留域名后缀 + 本机主机名，用于本机联调。
LOCAL_ONLY_SUFFIXES = (".test", ".example", ".invalid", ".localhost", ".local")
LOCAL_ONLY_HOSTS = {"localhost", "127.0.0.1", "::1"}

_TRUTHY = {"1", "true", "yes", "on", "y"}


def real_scan_enabled() -> bool:
    """真实外部扫描开关是否已显式开启。"""
    return os.getenv(REAL_SCAN_ENV, "").strip().lower() in _TRUTHY


def open_scan_enabled() -> bool:
    """「开放模式」（允许不挂授权范围直接扫描）是否生效。

    **缺省为关闭**：只有显式把 :data:`OPEN_SCAN_ENV` 设成真值
    （``1`` / ``true`` / ``yes`` / ``on`` / ``y``）才开启。未设置、空串、
    假值、以及**任何拼错的值**（例如 ``ture``）都按关闭处理 —— 失败关闭，
    不因为写错一个开关就静默进入开放公网扫描模式。

    它**不**放松真实扫描总开关：在这个开关开着、而 ``GEF_ALLOW_REAL_SCAN``
    没开的情况下，``real`` 任务照样 403；反过来，只开
    ``GEF_ALLOW_REAL_SCAN`` 而不开本开关，仍然必须有授权范围。
    """
    return os.getenv(OPEN_SCAN_ENV, "").strip().lower() in _TRUTHY


def resolve_mode(requested: str | None) -> str:
    """解析本次任务的执行模式。

    Args:
        requested: 请求中的 ``mode`` 字段，缺省视为 ``mock``。

    Returns:
        str: ``MODE_MOCK`` 或 ``MODE_REAL``。

    Raises:
        BadRequestError: ``mode`` 取值不在支持列表内。
        ScopeViolationError: 请求 ``real`` 但环境开关未开启。
    """
    mode = (requested or MODE_MOCK).strip().lower()
    if mode not in SUPPORTED_MODES:
        raise BadRequestError(f"mode 仅支持 {', '.join(SUPPORTED_MODES)}，收到: {requested!r}")
    if mode == MODE_REAL and not real_scan_enabled():
        raise ScopeViolationError(
            f"真实扫描未开启，需在 .env 中显式设置 {REAL_SCAN_ENV}=true 后重启服务",
            details={"env": REAL_SCAN_ENV},
        )
    return mode


def is_local_only_target(value: str) -> bool:
    """判断目标是否只指向本机或保留测试域名（不需要外网）。"""
    text = (value or "").strip().lower().rstrip(".")
    if not text:
        return False
    host = text.split("/", 1)[0]
    if ":" in host and host.count(":") == 1:
        host = host.split(":", 1)[0]
    if host in LOCAL_ONLY_HOSTS:
        return True
    if host.endswith(LOCAL_ONLY_SUFFIXES):
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return address.is_loopback or address.is_private


def mode_report() -> dict:
    """对外暴露的模式相关配置（不含路径与密钥）。"""
    return {
        "default_mode": MODE_MOCK,
        "supported_modes": list(SUPPORTED_MODES),
        "real_scan_enabled": real_scan_enabled(),
        "real_scan_env": REAL_SCAN_ENV,
        "open_scan_enabled": open_scan_enabled(),
        "open_scan_env": OPEN_SCAN_ENV,
    }
