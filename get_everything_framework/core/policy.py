"""统一 Policy / Scope Engine（P0-2）。

方案第 5.3、8 节要求：**Scope 判断不得散落在各个 API 与 Runner 内**，必须收敛成
一个统一入口，并在四个时机各确认一次：

```text
Job 创建        → validate_job_targets()
Job / Step 执行前 → validate_step_target()
网络目标解析后   → validate_resolved_address()
重定向目标       → validate_redirect_target()
```

本模块是上述四个入口的唯一实现，`api/scan.py`、`api/jobs.py`、`app.py` 与
`jobs/executor.py` 一律通过它做判断，不再各自写 ``scope.validate_target``。

设计取舍（重要，不要静默改动）：

* **无 Scope 即拒绝**：``scope_id`` 为空直接 400，不存在直接 403；
* **整体拒绝**：多目标里只要有一个越界，整个请求被拒，不做部分执行；
* **环回/私网/链路本地一律拒绝**，除非该地址被 ``allowed_cidrs`` **显式**放行
  —— 这是 SSRF 防护的核心，不能因为「本机联调」而放宽；
* **解析失败不阻断**：DNS 查不到时无法判断，交给 Runner 去报 ``network_error``。
  这条是有意为之：本机 fixture（``*.test``）与离线环境必须仍可跑通。
  解析**成功**时则逐个地址校验，命中禁止网段就拒绝。
* 校验函数不写数据库、不碰 Flask，便于在 worker / CLI / 测试中复用。
"""

from __future__ import annotations

import ipaddress
import socket
from typing import Callable
from urllib.parse import urlsplit

from core import scope_store
from core.errors import BadRequestError, InvalidTargetError, ScopeViolationError
from core.scope import Scope, normalize_target

#: DNS 解析器签名：``host -> [ip, ...]``。注入它可以让测试完全不碰网络。
Resolver = Callable[[str], list[str]]

#: 允许出现在重定向里的协议。``file:`` / ``gopher:`` / ``data:`` 等一律拒绝。
ALLOWED_REDIRECT_SCHEMES = ("http", "https")


# ── 地址分类 ──────────────────────────────────────────────


def is_dangerous_address(value: str) -> bool:
    """该地址是否属于「必须先显式放行才允许」的敏感网段。

    覆盖方案第 5.3 节点名的 localhost / loopback / link-local / 私网，
    外加保留段、组播与非指定地址（``0.0.0.0`` / ``::``）。
    """
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return True  # 解析不出地址：按「可疑」处理
    return bool(
        address.is_loopback
        or address.is_link_local
        or address.is_private
        or address.is_reserved
        or address.is_multicast
        or address.is_unspecified
    )


def resolve_host(host: str) -> list[str]:
    """解析主机的全部 A / AAAA 记录（去重保序）。

    解析失败（``socket.gaierror``）时返回空列表 —— 调用方据此判断「无法校验」。
    """
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, UnicodeError, OSError, ValueError):
        return []

    addresses: list[str] = []
    for info in infos:
        sockaddr = info[4]
        if not sockaddr:
            continue
        candidate = str(sockaddr[0])
        # IPv6 可能带 zone id（fe80::1%eth0），去掉后再判断。
        candidate = candidate.split("%", 1)[0]
        if candidate and candidate not in addresses:
            addresses.append(candidate)
    return addresses


# ── 入口 1：Job 创建 ──────────────────────────────────────


def require_scope(scope_id: str | None) -> Scope:
    """把 ``scope_id`` 解析成 Scope；缺失 400、不存在 403。

    Raises:
        BadRequestError: ``scope_id`` 为空。
        ScopeViolationError: Scope 不存在。
    """
    if not scope_id or not str(scope_id).strip():
        raise BadRequestError(
            "必须提供 scope_id：没有授权范围不允许创建扫描任务",
            details={"field": "scope_id"},
        )
    return scope_store.require(str(scope_id).strip())


def validate_job_targets(scope_id: str | None, targets: list[str]) -> tuple[Scope, list[str]]:
    """Job 创建时的统一校验：Scope 必须存在，目标必须全部在授权范围内。

    Args:
        scope_id: 请求中的授权范围 ID（M2 起必填）。
        targets: 待校验的原始目标列表。

    Returns:
        tuple[Scope, list[str]]: ``(scope, 规范化后的目标值列表)``。

    Raises:
        BadRequestError: ``scope_id`` 缺失，或目标列表为空。
        InvalidTargetError: 目标既不是合法域名也不是 IP/CIDR。
        ScopeViolationError: Scope 不存在，或任一目标越界（整体拒绝）。
    """
    scope = require_scope(scope_id)
    if not targets:
        raise BadRequestError("没有可扫描的目标")
    # validate_targets 逐个校验，任何一个失败即抛出，不会产生半成品结果。
    scoped = scope.validate_targets(targets)
    return scope, [item.value for item in scoped]


# ── 入口 2：Job / Step 执行前 ─────────────────────────────


def validate_step_target(scope_id: str | None, target: str) -> str:
    """Step 执行前的二次校验（方案第 5.3 节「每个 Step 执行前」）。

    Job 创建到真正执行之间，Scope 可能已被删除或收紧；因此执行期必须
    重新读一次 Scope 并重校验，而不是相信创建时的快照。

    Returns:
        str: 规范化后的目标值。

    Raises:
        BadRequestError / InvalidTargetError / ScopeViolationError: 同
            :func:`validate_job_targets`。
    """
    scope = require_scope(scope_id)
    return scope.validate_target(target).value


# ── 入口 3：网络目标最终解析后 ────────────────────────────


def validate_resolved_address(
    scope: Scope,
    host: str,
    *,
    resolver: Resolver | None = None,
) -> list[str]:
    """校验域名**实际解析出的地址**是否被授权（DNS/CNAME 变化防护）。

    这是防「授权域名被解析到内网地址」的关键一步：域名本身在 Scope 内，
    不代表它当前解析到的 IP 也安全。

    Args:
        scope: 已加载的 Scope。
        host: 待解析的主机名（也可以是字面量 IP）。
        resolver: 可注入的解析器，默认 :func:`resolve_host`。

    Returns:
        list[str]: 解析出的地址列表；**解析失败时为空列表**（调用方据此放行，
        由 Runner 去报网络错误）。

    Raises:
        ScopeViolationError: 任一解析结果落在敏感网段，且未被
            ``allowed_cidrs`` 显式放行。
    """
    resolver = resolver or resolve_host
    try:
        addresses = list(resolver(host) or [])
    except Exception:  # noqa: BLE001 - 解析器异常等同于「无法校验」
        return []

    for address in addresses:
        if not is_dangerous_address(address):
            continue
        # 敏感地址只有在 Scope 里被显式放行才允许。
        if not scope_address_allowed(scope, address):
            raise ScopeViolationError(
                f"目标 {host} 解析到受限地址 {address}，且该地址未被 Scope 放行",
                details={"target": host, "resolved": address, "scope_id": scope.id},
            )
    return addresses


def scope_address_allowed(scope: Scope, address: str) -> bool:
    """判断某个 IP 字面量是否落在 Scope 的 ``allowed_cidrs`` 内。"""
    try:
        parsed = ipaddress.ip_address(address)
    except ValueError:
        return False
    return any(parsed in network for network in scope._networks)  # noqa: SLF001 - 同一内核对象


# ── 入口 4：重定向目标 ────────────────────────────────────


def validate_redirect_target(scope: Scope, url: str) -> str:
    """校验一个重定向目标是否仍在本 Scope 内（重定向绕过防护）。

    拒绝规则：

    * 协议不在 :data:`ALLOWED_REDIRECT_SCHEMES` 内（``file:`` / ``gopher:`` 等）；
    * 主机部分越界或命中排除列表。

    Args:
        scope: 已加载的 Scope。
        url: 重定向响应里的 ``Location`` 值（可以是绝对 URL 或裸主机）。

    Returns:
        str: 通过校验的规范化主机名。

    Raises:
        InvalidTargetError: URL 为空、无主机或协议不被允许。
        ScopeViolationError: 主机越界。
    """
    text = (url or "").strip()
    if not text:
        raise InvalidTargetError("重定向目标不能为空")

    parsed = urlsplit(text)
    if parsed.scheme and parsed.scheme.lower() not in ALLOWED_REDIRECT_SCHEMES:
        raise InvalidTargetError(f"不允许的重定向协议: {parsed.scheme}")

    # 相对跳转（如 "/login"）不带主机，沿用原目标，无需再校验主机。
    if not parsed.scheme and not parsed.netloc:
        return ""

    host = parsed.hostname or ""
    if not host:
        raise InvalidTargetError(f"无法从重定向目标中解析出主机: {url!r}")

    target = normalize_target(host)
    try:
        return scope.validate_target(target.value).value
    except ScopeViolationError as exc:
        raise ScopeViolationError(
            f"重定向目标 {target.value} 越出授权范围",
            details={"redirect": url, "scope_id": scope.id},
        ) from exc
