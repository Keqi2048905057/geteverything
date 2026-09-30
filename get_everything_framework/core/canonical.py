"""资产规范化：统一 ``canonical_key`` 规则（P1，方案第 9 节）。

**要解决的问题**：同一台机器会在结果里以多种写法反复出现 ——

```text
HTTPS://Example.COM/
https://example.com
https://example.com:443/
```

如果直接拿原始字符串当去重键，这三条会变成**三个资产**，资产列表会被
「同一资产的 N 种写法」淹没，diff（方案第 10 节）也会满屏假新增。

**本模块的定位**：纯函数、零依赖（不碰 sqlite、不碰 Flask、不读配置），
只负责「一种资产类型 + 一个原始字符串 → 一个规范值 + 一个规范键」。
落库、去重、时间线在 :mod:`core.assets`，本模块只定义**规则本身**，
规则由 ``tests/unit/test_canonical.py`` 逐条锁定（方案第 9 节要求：
「这些规则必须写成测试，不能只存在开发者脑中」）。

**规则口径（显式写出来，避免以后靠猜）**：

============================  ==========================================================
类型                          规范化规则
============================  ==========================================================
``subdomain`` / ``host``      小写；去尾点；去首尾空白；IDNA 转 ASCII；校验 RFC 1123 标签
``ip``                        交给 :mod:`ipaddress` 压成标准写法（``2001:0db8::1`` → ``2001:db8::1``）
``cidr``                      :func:`ipaddress.ip_network` + ``strict=False``（``10.0.0.5/8`` → ``10.0.0.0/8``）
``url``                       协议小写；host 小写 + IDNA；**去默认端口**；空路径补 ``/``；
                              **保留 query，丢弃 fragment**
``port``                      ``host:port``，port 转 int（``:0443`` → ``:443``）
``service``                   小写 + 去空白（``HTTP`` → ``http``）
============================  ==========================================================

**为什么 fragment 要丢**：``#frag`` 只对浏览器有意义，永远不会发给服务端；
同一条 URL 带不带 fragment，服务端看到的是同一个资源，算成两个资产是假差异。

**为什么 query 要留**：``/?id=1`` 与 ``/?id=2`` 服务端看到的是两个资源，
合并会把真实的资产变化抹掉。

**默认端口为什么要去掉**：``https://example.com:443/`` 与 ``https://example.com``
在网络上完全等价，不去掉就永远去不了重（方案第 9 节的验收例子就是这三条）。
"""

from __future__ import annotations

import ipaddress
import re
from urllib.parse import urlsplit, urlunsplit

# ── 类型常量 ──────────────────────────────────────────────

TYPE_SUBDOMAIN = "subdomain"
TYPE_HOST = "host"
TYPE_IP = "ip"
TYPE_CIDR = "cidr"
TYPE_URL = "url"
TYPE_PORT = "port"
TYPE_SERVICE = "service"

#: 方案第 9 节要求的七种类型。
ASSET_TYPES = (
    TYPE_SUBDOMAIN,
    TYPE_HOST,
    TYPE_IP,
    TYPE_CIDR,
    TYPE_URL,
    TYPE_PORT,
    TYPE_SERVICE,
)

#: 协议 → 默认端口。仅这两种协议会做「去默认端口」；其它协议一律保留显式端口。
DEFAULT_PORTS = {"http": 80, "https": 443}

#: 只允许这两种协议进 canonical key：``file://`` / ``javascript:`` 之类
#: 既不是资产也不该被当成 URL 资产收进来。
ALLOWED_SCHEMES = ("http", "https")

# 主机名标签：RFC 1123 允许字母/数字/连字符；额外放行下划线（DNS 记录里常见，
# 如 ``_dmarc.example.com``），但标签不能以连字符开头或结尾。
_LABEL_RE = re.compile(r"^(?!-)[A-Za-z0-9_-]{1,63}(?<!-)$")

MAX_HOSTNAME_LENGTH = 253


class CanonicalError(ValueError):
    """无法把给定值归一到该资产类型。

    继承 ``ValueError``：调用方（尤其是遍历工具输出的解析路径）按
    「这条数据不合格，跳过」处理即可，不需要单独 import 本异常。
    """


def _text(value: object) -> str:
    """统一入参：``None`` / bytes / 数字都转成干净字符串。"""
    if value is None:
        return ""
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    return str(value).strip()


def _idna(host: str) -> str:
    """把主机名转成小写 ASCII（unicode 域名 → punycode）。

    失败时原样返回小写结果：宁可用「没转成功的字符串」也不静默丢弃一条资产，
    但调用方仍会经过 :func:`_validate_host` 的标签校验。
    """
    lowered = host.lower()
    try:
        return lowered.encode("idna").decode("ascii")
    except (UnicodeError, UnicodeDecodeError):
        return lowered


def _validate_host(host: str, *, original: str) -> str:
    """校验并规范化一个主机名（不含端口、不含协议）。"""
    if not host:
        raise CanonicalError(f"主机名为空: {original!r}")
    if len(host) > MAX_HOSTNAME_LENGTH:
        raise CanonicalError(f"主机名过长（>{MAX_HOSTNAME_LENGTH}）: {original!r}")

    candidate = host[:-1] if host.endswith(".") else host
    if not candidate:
        raise CanonicalError(f"主机名为空: {original!r}")

    for label in candidate.split("."):
        if not _LABEL_RE.match(label):
            raise CanonicalError(f"非法的主机名标签 {label!r}: {original!r}")
    return candidate


def normalize_host(value: object) -> str:
    """规范化主机名 / 子域名（去空白、小写、去尾点、IDNA、标签校验）。"""
    raw = _text(value)
    if not raw:
        raise CanonicalError("主机名为空")
    # 容忍被误传进来的 ``host:port`` 与 ``scheme://host``：只取主机部分，
    # 而不是报错——上游解析器拿到的字段经常是整条 URL。
    if "://" in raw:
        raw = _text(urlsplit(raw).hostname or "")
    if raw.startswith("[") and "]" in raw:  # IPv6 字面量
        raw = raw[1 : raw.index("]")]
    elif raw.count(":") == 1:
        raw = raw.split(":", 1)[0]
    return _validate_host(_idna(raw), original=_text(value))


def normalize_ip(value: object) -> str:
    """规范化 IP 地址（IPv4 / IPv6 都压成标准写法）。"""
    raw = _text(value)
    if not raw:
        raise CanonicalError("IP 为空")
    if raw.startswith("[") and raw.endswith("]"):
        raw = raw[1:-1]
    try:
        return str(ipaddress.ip_address(raw))
    except ValueError as exc:
        raise CanonicalError(f"非法 IP: {value!r}") from exc


def normalize_cidr(value: object) -> str:
    """规范化网段。

    ``strict=False`` 是刻意的：``10.0.0.5/8`` 这类「主机位不为 0」的写法在
    扫描结果里很常见，它的意思就是 ``10.0.0.0/8``。报错会让上游少收一条资产，
    而照抄原样又会造成同一个网段的多种写法各自成资产。
    """
    raw = _text(value)
    if not raw:
        raise CanonicalError("网段为空")
    try:
        return str(ipaddress.ip_network(raw, strict=False))
    except ValueError as exc:
        raise CanonicalError(f"非法网段: {value!r}") from exc


def normalize_url(value: object) -> str:
    """规范化 URL。

    规则见模块文档。缺协议时按 ``http`` 补全（``example.com/path`` →
    ``http://example.com/path``），而不是报错：爬虫与工具输出里
    「裸 host+path」很常见，报错等于漏收资产。
    """
    raw = _text(value)
    if not raw:
        raise CanonicalError("URL 为空")

    if "://" not in raw:
        # ``javascript:alert(1)`` 这类「有协议但没有 //」的写法必须先识别出来，
        # 否则会被补成 ``http://javascript:alert(1)`` 而蒙混过关。
        scheme_like = re.match(r"^([A-Za-z][A-Za-z0-9+.-]*):", raw)
        if scheme_like:
            bad = scheme_like.group(1).lower()
            if bad not in ALLOWED_SCHEMES:
                raise CanonicalError(f"不支持的 URL 协议 {bad!r}: {value!r}")
        raw = f"http://{raw}"

    parts = urlsplit(raw)
    scheme = parts.scheme.lower()
    if scheme not in ALLOWED_SCHEMES:
        raise CanonicalError(f"不支持的 URL 协议 {scheme!r}: {value!r}")

    host = _validate_host(_idna(parts.hostname or ""), original=_text(value))

    # 端口：显式默认端口视为没写；其余保留数字形态（不保留 ``:0443``）。
    # ``parts.port`` 在端口非法（非数字 / 越界）时抛 ``ValueError``，要转成
    # CanonicalError，否则调用方得同时 catch 两种异常。
    try:
        port = parts.port
    except ValueError as exc:
        raise CanonicalError(f"非法端口: {value!r}") from exc
    if port == DEFAULT_PORTS[scheme]:
        port = None
    if port is not None and not (0 < port <= 65535):
        raise CanonicalError(f"非法端口 {port}: {value!r}")

    netloc = host if port is None else f"{host}:{port}"
    if parts.username or parts.password:
        # 带凭据的 URL 不进 canonical key：凭据属于「怎么访问」，
        # 不属于「资产是什么」，而且入库会变成凭证泄露面。
        raise CanonicalError(f"URL 不得携带凭据: {value!r}")

    path = parts.path or "/"
    if not path.startswith("/"):
        path = f"/{path}"

    # fragment 丢弃（见模块文档），query 保留。
    return urlunsplit((scheme, netloc, path, parts.query, ""))


def normalize_port(value: object) -> str:
    """规范化 ``host:port``。

    只接受 ``host:port`` 形态；单独一个数字无法判断属于哪台主机，
    因此宁可报错也不猜。
    """
    raw = _text(value)
    if not raw:
        raise CanonicalError("端口为空")

    if raw.startswith("["):  # IPv6 字面量：[::1]:8080
        end = raw.find("]")
        if end < 0 or not raw[end + 1 :].startswith(":"):
            raise CanonicalError(f"非法端口写法: {value!r}")
        host_text = raw[1:end]
        port_text = raw[end + 2 :]
        host = normalize_ip(host_text)
        host = f"[{host}]"
    else:
        if raw.count(":") != 1:
            raise CanonicalError(f"端口必须写成 host:port: {value!r}")
        host_text, port_text = raw.split(":", 1)
        host = normalize_host(host_text)

    port_text = port_text.strip()
    if not port_text.isdigit():
        raise CanonicalError(f"非法端口: {value!r}")
    port = int(port_text, 10)
    if not (0 < port <= 65535):
        raise CanonicalError(f"端口越界: {value!r}")
    return f"{host}:{port}"


def normalize_service(value: object) -> str:
    """规范化服务名（``HTTP`` → ``http``）。

    允许 ``/``：部分工具会把复合服务写成 ``ssl/vpn`` 这种组合形式，
    直接拒绝会让这条观测被静默丢掉。但首字符必须是字母或数字。
    """
    raw = _text(value).lower()
    if not raw:
        raise CanonicalError("服务名为空")
    if not re.match(r"^[a-z0-9][a-z0-9._+/-]*$", raw):
        raise CanonicalError(f"非法的服务名: {value!r}")
    return raw


_NORMALIZERS = {
    TYPE_SUBDOMAIN: normalize_host,
    TYPE_HOST: normalize_host,
    TYPE_IP: normalize_ip,
    TYPE_CIDR: normalize_cidr,
    TYPE_URL: normalize_url,
    TYPE_PORT: normalize_port,
    TYPE_SERVICE: normalize_service,
}


def normalize(asset_type: str, value: object) -> str:
    """按类型把原始值规范化。

    Args:
        asset_type: :data:`ASSET_TYPES` 之一。
        value: 原始字符串。

    Returns:
        str: 规范值（写入 ``assets.value`` 的那一份）。

    Raises:
        CanonicalError: 类型未知，或值无法归一。
    """
    key = _text(asset_type).lower()
    normalizer = _NORMALIZERS.get(key)
    if normalizer is None:
        raise CanonicalError(f"未知的资产类型 {asset_type!r}，支持 {', '.join(ASSET_TYPES)}")
    return normalizer(value)


def canonical_key(asset_type: str, value: object) -> str:
    """``类型|规范值`` 形式的去重键。

    **类型必须进键**：``example.com`` 作为 ``host`` 与作为 ``subdomain``
    是两条不同来源的资产，混在一起会让「这个资产是谁发现的」失去意义。
    """
    key = _text(asset_type).lower()
    return f"{key}|{normalize(key, value)}"


def guess_type(value: object) -> str | None:
    """猜一个值的类型（只用于**信息展示与旧数据兜底**，不用于去重）。

    去重路径必须由调用方给出明确类型（``Observation.category`` /
    ``TOOL_DATABASES`` 映射），因为猜测会让同一份数据在不同代码路径下落成
    不同类型，反而制造假重复。返回 ``None`` 表示猜不出来。
    """
    raw = _text(value)
    if not raw:
        return None
    lowered = raw.lower()

    # 先判 IP：``1.2.3.4`` 同时能通过 ``ip_address`` 与 ``ip_network``
    # （后者会当成 /32），先判网段会把纯 IP 误标成 cidr。
    try:
        normalize_ip(lowered)
        return TYPE_IP
    except CanonicalError:
        pass

    # 网段必须显式带 ``/``，否则「10.0.0.0」这种裸地址只会被当成 IP。
    # 注意：这里**不能**在解析失败时直接 ``return None`` —— ``https://example.com/``
    # 也带 ``/``，那样会把 URL 误判成「猜不出来」。
    if "/" in lowered:
        try:
            normalize_cidr(lowered)
            return TYPE_CIDR
        except CanonicalError:
            pass

    if "://" in lowered:
        try:
            normalize_url(lowered)
            return TYPE_URL
        except CanonicalError:
            return None

    if lowered.count(":") == 1:
        try:
            normalize_port(lowered)
            return TYPE_PORT
        except CanonicalError:
            pass

    try:
        normalize_host(lowered)
    except CanonicalError:
        return None
    return TYPE_SUBDOMAIN
