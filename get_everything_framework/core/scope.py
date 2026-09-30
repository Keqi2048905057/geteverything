"""授权范围（Scope）模型与目标校验。

本机联调版的硬性约束（方案第 2.3 节）：

* 没有显式 Scope 时，禁止创建扫描任务；
* 目标在执行前必须通过 Scope 校验；
* ``active_scan=False`` 时只允许本机 fixture 等被动测试，不允许真实外部扫描。

设计要点：

* 目标先被规范化（小写、去 scheme / 端口 / 路径、去尾点），再做匹配；
* 「排除优先」——命中 ``excluded_domains`` 的目标一律拒绝；
* 匹配采用「相等或以 ``.<allowed>`` 结尾」，即 ``example.test`` 覆盖
  ``a.example.test``，但不覆盖 ``notexample.test``；
* 拒绝 ``*`` 这类全放行通配符，必须显式列出允许的域名或网段；
* IP 目标只能由 ``allowed_cidrs`` 放行。
"""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass, field
from typing import Iterable
from urllib.parse import urlsplit

from core.errors import InvalidTargetError, ScopeViolationError

# 域名标签：字母/数字开头结尾，中间允许连字符，总长 1..63。
_LABEL_RE = re.compile(r"^(?!-)[a-z0-9-]{1,63}(?<!-)$")

_ALLOW_ALL_TOKENS = {"*", "*.*", "0.0.0.0/0", "::/0"}

TARGET_KIND_DOMAIN = "domain"
TARGET_KIND_IP = "ip"


@dataclass(frozen=True)
class NormalizedTarget:
    """规范化后的扫描目标。"""

    raw: str
    kind: str
    value: str

    def to_dict(self) -> dict:
        return {"raw": self.raw, "kind": self.kind, "value": self.value}


def _strip_scheme_and_path(raw: str) -> str:
    """从 URL / host:port / 裸主机名中提取主机部分。"""
    text = raw.strip()
    if "://" in text:
        parsed = urlsplit(text)
        text = parsed.netloc or parsed.path
    # 去掉可能残留的 userinfo
    if "@" in text:
        text = text.rsplit("@", 1)[1]
    # 去掉端口（IPv6 需保留方括号内的冒号）
    if text.startswith("["):
        end = text.find("]")
        return text[1:end] if end != -1 else text
    if text.count(":") == 1:
        text = text.split(":", 1)[0]
    return text


def _is_valid_domain(value: str) -> bool:
    if not value or len(value) > 253 or "." not in value:
        return False
    labels = value.rstrip(".").split(".")
    return all(_LABEL_RE.match(label) for label in labels)


def normalize_target(raw: str) -> NormalizedTarget:
    """把用户输入的目标规范化为 ``NormalizedTarget``。

    Args:
        raw: 原始目标字符串，可以是域名、IP、CIDR、URL 或 ``host:port``。

    Returns:
        NormalizedTarget: 规范化结果。

    Raises:
        InvalidTargetError: 目标为空、格式非法或含控制字符。
    """
    if raw is None:
        raise InvalidTargetError("目标不能为空")

    text = str(raw).strip().lower()
    if not text:
        raise InvalidTargetError("目标不能为空")
    if any(ch.isspace() or ord(ch) < 32 for ch in text):
        raise InvalidTargetError(f"目标包含空白或控制字符: {raw!r}")

    host = _strip_scheme_and_path(text).strip().rstrip(".")
    if not host:
        raise InvalidTargetError(f"无法从目标中解析出主机: {raw!r}")

    # CIDR 只在 Scope 的 allowed_cidrs 里出现，作为扫描目标时取其网络地址。
    if "/" in host:
        try:
            network = ipaddress.ip_network(host, strict=False)
        except ValueError as exc:
            raise InvalidTargetError(f"非法的 CIDR: {host}") from exc
        return NormalizedTarget(raw=raw, kind=TARGET_KIND_IP, value=str(network.network_address))

    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        if not _is_valid_domain(host):
            raise InvalidTargetError(f"非法目标（既不是合法域名也不是 IP）: {raw!r}") from None
        return NormalizedTarget(raw=raw, kind=TARGET_KIND_DOMAIN, value=host)

    return NormalizedTarget(raw=raw, kind=TARGET_KIND_IP, value=str(ip))


def _matches_domain(host: str, pattern: str) -> bool:
    """判断 host 是否命中某个域名模式。

    支持两种写法：

    * ``example.test``    —— 匹配自身及其子域；
    * ``*.example.test``  —— 只匹配子域，不匹配 ``example.test`` 自身。
    """
    pattern = pattern.strip().lower().rstrip(".")
    if not pattern:
        return False
    if pattern.startswith("*."):
        base = pattern[2:]
        return bool(base) and host.endswith("." + base)
    return host == pattern or host.endswith("." + pattern)


def _parse_cidrs(values: Iterable[str]) -> list:
    networks = []
    for value in values:
        text = str(value).strip()
        if not text:
            continue
        if text in _ALLOW_ALL_TOKENS:
            raise InvalidTargetError(f"不允许使用全放行网段: {text}")
        try:
            network = ipaddress.ip_network(text, strict=False)
        except ValueError as exc:
            raise InvalidTargetError(f"Scope 中存在非法 CIDR: {text}") from exc
        # /0 等价于放行整个地址族，同样视为全放行。
        if network.prefixlen == 0:
            raise InvalidTargetError(f"不允许使用全放行网段: {text}")
        networks.append(network)
    return networks


@dataclass
class Scope:
    """授权范围。

    Attributes:
        id: Scope 主键。
        name: 人类可读名称。
        allowed_domains: 允许的域名/子域模式。
        allowed_cidrs: 允许的 IP 网段。
        excluded_domains: 强制排除的域名/子域模式。
        active_scan: 是否允许真实外部扫描。
        created_at / created_by: 审计字段。
    """

    id: str
    name: str
    allowed_domains: list[str] = field(default_factory=list)
    allowed_cidrs: list[str] = field(default_factory=list)
    excluded_domains: list[str] = field(default_factory=list)
    active_scan: bool = False
    created_at: str | None = None
    created_by: str | None = None

    def __post_init__(self):
        self.allowed_domains = [str(d).strip().lower() for d in self.allowed_domains if str(d).strip()]
        self.excluded_domains = [str(d).strip().lower() for d in self.excluded_domains if str(d).strip()]
        self.allowed_cidrs = [str(c).strip() for c in self.allowed_cidrs if str(c).strip()]

        if not self.name or not str(self.name).strip():
            raise InvalidTargetError("Scope 必须提供 name")
        if not self.allowed_domains and not self.allowed_cidrs:
            raise InvalidTargetError("Scope 至少需要一项 allowed_domains 或 allowed_cidrs")

        for pattern in [*self.allowed_domains, *self.excluded_domains]:
            if pattern in _ALLOW_ALL_TOKENS:
                raise InvalidTargetError("不允许使用全放行通配符，请显式列出允许的域名")
            bare = pattern[2:] if pattern.startswith("*.") else pattern
            if not _is_valid_domain(bare):
                raise InvalidTargetError(f"Scope 中存在非法域名模式: {pattern}")

        # 预先解析网段，尽早暴露配置错误。
        self._networks = _parse_cidrs(self.allowed_cidrs)

    # ── 校验 ─────────────────────────────────────────────

    def validate_target(self, raw: str) -> NormalizedTarget:
        """校验单个目标是否落在授权范围内。

        Returns:
            NormalizedTarget: 通过校验的规范化目标。

        Raises:
            InvalidTargetError: 目标格式非法。
            ScopeViolationError: 目标不在允许范围内，或命中排除列表。
        """
        target = normalize_target(raw)

        if target.kind == TARGET_KIND_DOMAIN:
            if any(_matches_domain(target.value, p) for p in self.excluded_domains):
                raise ScopeViolationError(
                    f"目标 {target.value} 命中排除列表",
                    details={"target": target.value, "scope_id": self.id},
                )
            if any(_matches_domain(target.value, p) for p in self.allowed_domains):
                return target
            raise ScopeViolationError(
                f"目标 {target.value} 不在 Scope 允许的域名内",
                details={"target": target.value, "scope_id": self.id},
            )

        # IP 目标：先看是否命中被排除域名对应的反查结果（此处不做 DNS 反查，
        # 只用网段判断），再要求落在 allowed_cidrs 内。
        address = ipaddress.ip_address(target.value)
        if any(address in network for network in self._networks):
            return target
        raise ScopeViolationError(
            f"目标 {target.value} 不在 Scope 允许的网段内",
            details={"target": target.value, "scope_id": self.id},
        )

    def validate_targets(self, raws: Iterable[str]) -> list[NormalizedTarget]:
        """批量校验；任何一个不通过就整体抛出。"""
        return [self.validate_target(raw) for raw in raws]

    def allows_active_scan(self) -> bool:
        """是否允许真实外部扫描。"""
        return bool(self.active_scan)

    def require_active_scan(self) -> None:
        """真实扫描模式的守卫。

        Raises:
            ScopeViolationError: Scope 未开启 ``active_scan``。
        """
        if not self.active_scan:
            raise ScopeViolationError(
                "该 Scope 未开启 active_scan，禁止执行真实外部扫描",
                details={"scope_id": self.id, "name": self.name},
            )

    # ── 序列化 ───────────────────────────────────────────

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "allowed_domains": list(self.allowed_domains),
            "allowed_cidrs": list(self.allowed_cidrs),
            "excluded_domains": list(self.excluded_domains),
            "active_scan": bool(self.active_scan),
            "created_at": self.created_at,
            "created_by": self.created_by,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Scope":
        """从 API 请求体或数据库行构造 Scope。"""
        return cls(
            id=data.get("id") or "",
            name=data.get("name") or "",
            allowed_domains=list(data.get("allowed_domains") or []),
            allowed_cidrs=list(data.get("allowed_cidrs") or []),
            excluded_domains=list(data.get("excluded_domains") or []),
            active_scan=bool(data.get("active_scan", False)),
            created_at=data.get("created_at"),
            created_by=data.get("created_by"),
        )
