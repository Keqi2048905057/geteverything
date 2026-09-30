"""P1 单元测试：资产规范化（``core.canonical``，方案第 9 节）。

方案第 9 节的原话：**「这些规则必须写成测试，不能只存在开发者脑中。」**
本文件即那份规则的可执行版本 —— 每一个「看起来应该合并」和
「看起来不该合并」的边界都在这里。

方案给出的验收例子（必须归一成同一个资产）::

    HTTPS://Example.COM/
    https://example.com
    https://example.com:443/
"""

import pytest

from core.canonical import (
    ASSET_TYPES,
    CanonicalError,
    canonical_key,
    guess_type,
    normalize,
    normalize_cidr,
    normalize_host,
    normalize_ip,
    normalize_port,
    normalize_service,
    normalize_url,
)


# ── 方案第 9 节的验收例子 ─────────────────────────────────


@pytest.mark.parametrize(
    "raw",
    [
        "HTTPS://Example.COM/",
        "https://example.com",
        "https://example.com:443/",
        "https://EXAMPLE.com:443",
        "  https://example.com/  ",
    ],
)
def test_plan_acceptance_urls_collapse_to_one(raw):
    """方案第 9 节验收：这三种写法必须是**同一个** canonical key。"""
    assert normalize_url(raw) == "https://example.com/"


def test_plan_acceptance_three_forms_share_one_key():
    keys = {
        canonical_key("url", "HTTPS://Example.COM/"),
        canonical_key("url", "https://example.com"),
        canonical_key("url", "https://example.com:443/"),
    }
    assert len(keys) == 1


# ── host / subdomain ─────────────────────────────────────


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Example.COM", "example.com"),
        ("example.com.", "example.com"),
        ("  example.com  ", "example.com"),
        ("SUB.Example.COM.", "sub.example.com"),
        ("_dmarc.example.com", "_dmarc.example.com"),  # 下划线标签放行
        ("xn--fsq.com", "xn--fsq.com"),
        ("例子.com", "xn--fsqu00a.com"),  # IDNA 转 punycode
    ],
)
def test_normalize_host(raw, expected):
    assert normalize_host(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "   ",
        "-leading.example.com",
        "trailing-.example.com",
        "bad space.com",
        "a" * 64 + ".com",  # 单个标签超过 63
        "x" * 250 + ".com",  # 总长超过 253
    ],
)
def test_normalize_host_rejects(raw):
    with pytest.raises(CanonicalError):
        normalize_host(raw)


def test_normalize_host_accepts_url_and_hostport():
    """上游字段经常直接把整条 URL 塞进来，取主机部分而不是报错。"""
    assert normalize_host("https://Example.com:8443/path") == "example.com"
    assert normalize_host("example.com:8080") == "example.com"


def test_subdomain_and_host_are_distinct_keys():
    """类型必须进键：同一个字符串作为 host 与 subdomain 是两条来源不同的资产。"""
    assert canonical_key("host", "example.com") != canonical_key("subdomain", "example.com")


# ── ip / cidr ────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("127.0.0.1", "127.0.0.1"),
        ("  127.0.0.1  ", "127.0.0.1"),
        ("2001:0db8:0000:0000:0000:0000:0000:0001", "2001:db8::1"),
        ("2001:DB8::1", "2001:db8::1"),
        ("[::1]", "::1"),
    ],
)
def test_normalize_ip(raw, expected):
    assert normalize_ip(raw) == expected


@pytest.mark.parametrize("raw", ["", "999.1.1.1", "not-an-ip", "1.2.3", "1.2.3.4.5"])
def test_normalize_ip_rejects(raw):
    with pytest.raises(CanonicalError):
        normalize_ip(raw)


def test_normalize_cidr_host_bits_are_trimmed_not_rejected():
    """``10.0.0.5/8`` 在真实输出里很常见，它的意思就是 ``10.0.0.0/8``。"""
    assert normalize_cidr("10.0.0.5/8") == "10.0.0.0/8"
    assert normalize_cidr("192.168.1.7/24") == "192.168.1.0/24"
    assert normalize_cidr("2001:db8::5/32") == "2001:db8::/32"


def test_cidr_and_ip_keys_differ():
    assert canonical_key("cidr", "10.0.0.0/8") != canonical_key("ip", "10.0.0.1")


# ── url ──────────────────────────────────────────────────


def test_url_default_port_is_dropped():
    assert normalize_url("http://example.com:80/") == "http://example.com/"
    assert normalize_url("https://example.com:443/") == "https://example.com/"
    assert normalize_url("https://example.com:8443/") == "https://example.com:8443/"


def test_url_missing_scheme_defaults_to_http():
    assert normalize_url("example.com/path") == "http://example.com/path"


def test_url_empty_path_becomes_slash():
    assert normalize_url("https://example.com") == "https://example.com/"
    assert normalize_url("https://example.com?x=1") == "https://example.com/?x=1"


def test_url_query_is_kept_because_it_is_a_different_resource():
    """``/?id=1`` 与 ``/?id=2`` 服务端看到的是两个资源，不能合并。"""
    assert normalize_url("https://example.com/?id=1") != normalize_url("https://example.com/?id=2")
    assert normalize_url("https://example.com/?a=1&b=2") == "https://example.com/?a=1&b=2"


def test_url_fragment_is_dropped_because_server_never_sees_it():
    assert normalize_url("https://example.com/page#section") == "https://example.com/page"
    assert normalize_url("https://example.com/page#other") == "https://example.com/page"


def test_url_host_is_case_folded_and_idna_encoded():
    assert normalize_url("HTTPS://ExAmPlE.CoM/Path") == "https://example.com/Path"
    assert normalize_url("https://例子.com/") == "https://xn--fsqu00a.com/"


def test_url_path_case_is_preserved():
    """路径**大小写敏感**（Linux 上 /Path 与 /path 是两个资源），不能一起小写。"""
    assert normalize_url("https://example.com/Path") != normalize_url("https://example.com/path")


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "ftp://example.com/",  # 协议白名单外
        "javascript:alert(1)",
        "https://user:pass@example.com/",  # 凭据不入 canonical key
        "https://example.com:99999/",  # 端口越界
    ],
)
def test_normalize_url_rejects(raw):
    with pytest.raises(CanonicalError):
        normalize_url(raw)


# ── port / service ───────────────────────────────────────


def test_normalize_port():
    assert normalize_port("Example.com:443") == "example.com:443"
    assert normalize_port("example.com:0443") == "example.com:443"  # 前导零归一
    assert normalize_port("[2001:db8::1]:8080") == "[2001:db8::1]:8080"


@pytest.mark.parametrize("raw", ["", "80", "example.com:", "example.com:abc", "example.com:0", "a:b:c"])
def test_normalize_port_rejects(raw):
    with pytest.raises(CanonicalError):
        normalize_port(raw)


def test_normalize_service():
    assert normalize_service("HTTP") == "http"
    assert normalize_service("  ssl/vpn  ") == "ssl/vpn"


@pytest.mark.parametrize("raw", ["", "   ", "-bad", "bad name", "/leading"])
def test_normalize_service_rejects(raw):
    with pytest.raises(CanonicalError):
        normalize_service(raw)


# ── 统一入口 ─────────────────────────────────────────────


@pytest.mark.parametrize("asset_type", ASSET_TYPES)
def test_normalize_dispatches_every_declared_type(asset_type):
    """ASSET_TYPES 里每个类型都必须真的能调通（防止声明与实现脱节）。"""
    samples = {
        "subdomain": "a.example.com",
        "host": "example.com",
        "ip": "10.0.0.1",
        "cidr": "10.0.0.0/8",
        "url": "https://example.com/",
        "port": "example.com:443",
        "service": "http",
    }
    assert normalize(asset_type, samples[asset_type]) == samples[asset_type]


def test_normalize_unknown_type_rejected():
    with pytest.raises(CanonicalError, match="未知的资产类型"):
        normalize("galaxy", "whatever")


def test_canonical_key_shape():
    assert canonical_key("url", "HTTPS://Example.COM/") == "url|https://example.com/"
    assert canonical_key("URL", "HTTPS://Example.COM/") == "url|https://example.com/"


def test_canonical_key_normalizes_whitespace_and_bytes():
    assert canonical_key("host", b" Example.COM. ") == "host|example.com"


# ── guess_type（仅展示/兜底用） ──────────────────────────


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("10.0.0.1", "ip"),
        ("10.0.0.0/8", "cidr"),
        ("https://example.com/", "url"),
        ("example.com:443", "port"),
        ("example.com", "subdomain"),
        ("", None),
        ("bad space.com", None),
        ("file:///etc/passwd", None),
    ],
)
def test_guess_type(raw, expected):
    assert guess_type(raw) == expected


def test_guess_type_does_not_claim_cidr_for_plain_ip():
    """回归：裸 IP 曾经被 ``ip_network(strict=False)`` 接受而误判成 cidr。"""
    assert guess_type("10.0.0.1") == "ip"
    assert guess_type("10.0.0.1/32") == "cidr"
