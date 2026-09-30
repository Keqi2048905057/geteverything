"""扫描模式与真实扫描开关。

方案第 2.3 节的硬性约束：

* 默认使用 mock 工具模式完成自动化测试（第 9 条）；
* 真实外部工具执行必须显式开启（第 8 条）。

本模块提供唯一的判定入口，避免各处在代码里散落 ``os.getenv("...")``：

* :func:`real_scan_enabled` —— 读环境开关 ``GEF_ALLOW_REAL_SCAN``；
* :func:`resolve_mode` —— 把请求里的 ``mode`` 解析成 ``mock`` / ``real``，
  未显式开启开关时拒绝 ``real``；
* :func:`is_local_only_target` —— 判断目标是否只指向本机/保留域名，
  供 Scope 创建时的提示与测试使用。
"""

from __future__ import annotations

import ipaddress
import os

from core.errors import BadRequestError, ScopeViolationError

# 环境变量：只有显式设为真值时才允许真实外部扫描。
REAL_SCAN_ENV = "GEF_ALLOW_REAL_SCAN"

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
    }
