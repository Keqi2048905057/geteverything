"""M1 单元测试：Scope 校验与执行模式开关。

只测纯逻辑，不启动 Web、不发网络请求、不碰仓库里的数据库。
"""

import pytest

from core.errors import InvalidTargetError, ScopeViolationError
from core.scope import Scope, normalize_target
from core.safety import MODE_MOCK, MODE_REAL, REAL_SCAN_ENV, is_local_only_target, resolve_mode


def make_scope(**overrides):
    payload = {
        "id": "scope_test",
        "name": "本地测试范围",
        "allowed_domains": ["example.test"],
        "allowed_cidrs": ["127.0.0.1/32"],
        "excluded_domains": [],
        "active_scan": False,
    }
    payload.update(overrides)
    return Scope(**payload)


# ── 目标规范化 ────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Example.TEST", "example.test"),
        ("https://a.example.test/path?q=1", "a.example.test"),
        ("a.example.test:8443", "a.example.test"),
        ("user@a.example.test:8080", "a.example.test"),
        ("a.example.test.", "a.example.test"),
        ("127.0.0.1", "127.0.0.1"),
    ],
)
def test_normalize_target(raw, expected):
    assert normalize_target(raw).value == expected


@pytest.mark.parametrize("raw", ["", "   ", "not a host", "http://", "-bad-.test", "a..b"])
def test_normalize_target_rejects_invalid(raw):
    with pytest.raises(InvalidTargetError):
        normalize_target(raw)


@pytest.mark.parametrize(
    "raw,expected",
    [
        # 方案第 6 节：用户输入是「域名 / IP / URL」—— 没写协议的 URL 也算 URL。
        # 不写这条分支时 ``www.example.test/a/b`` 会掉进 CIDR 分支，
        # 报出「非法的 CIDR」，把一条正常输入说成网段写错了。
        ("www.example.test/a/b", "www.example.test"),
        ("example.test/", "example.test"),
        ("example.test/a?q=1", "example.test"),
    ],
)
def test_normalize_target_accepts_schemeless_url(raw, expected):
    assert normalize_target(raw).value == expected


def test_normalize_target_still_reports_a_broken_cidr_as_cidr():
    """把「主机 + 路径」与「网段」分开之后，真正的坏网段仍要如实报网段错。

    这条防的是「顺手把后缀是数字的全都当路径」那种过度修正：
    ``192.0.2.0/99`` 的掩码非法，报的必须是 CIDR 而不是「非法域名」。
    """
    with pytest.raises(InvalidTargetError) as excinfo:
        normalize_target("192.0.2.0/99")
    assert "CIDR" in excinfo.value.message


# ── Scope 构造约束 ────────────────────────────────────────


def test_scope_requires_name():
    with pytest.raises(InvalidTargetError):
        make_scope(name="")


def test_scope_requires_at_least_one_allow_rule():
    with pytest.raises(InvalidTargetError):
        make_scope(allowed_domains=[], allowed_cidrs=[])


@pytest.mark.parametrize("pattern", ["*", "0.0.0.0/0"])
def test_scope_rejects_allow_all(pattern):
    if "/" in pattern:
        with pytest.raises(InvalidTargetError):
            make_scope(allowed_domains=[], allowed_cidrs=[pattern])
    else:
        with pytest.raises(InvalidTargetError):
            make_scope(allowed_domains=[pattern])


def test_scope_rejects_invalid_cidr():
    with pytest.raises(InvalidTargetError):
        make_scope(allowed_cidrs=["127.0.0.1/99"])


# ── Scope 匹配语义 ────────────────────────────────────────


def test_exact_domain_allowed():
    scope = make_scope()
    assert scope.validate_target("example.test").value == "example.test"


def test_subdomain_allowed():
    scope = make_scope()
    assert scope.validate_target("a.b.example.test").value == "a.b.example.test"


def test_sibling_domain_rejected():
    scope = make_scope()
    with pytest.raises(ScopeViolationError):
        scope.validate_target("notexample.test")


def test_wildcard_matches_subdomain_only():
    scope = make_scope(allowed_domains=["*.example.test"])
    assert scope.validate_target("a.example.test").value == "a.example.test"
    with pytest.raises(ScopeViolationError):
        scope.validate_target("example.test")


def test_excluded_domain_wins_over_allow():
    scope = make_scope(excluded_domains=["secret.example.test"])
    with pytest.raises(ScopeViolationError):
        scope.validate_target("secret.example.test")
    # 排除的是子域，父域仍可扫描
    assert scope.validate_target("example.test").value == "example.test"


def test_ip_allowed_by_cidr():
    scope = make_scope(allowed_domains=[], allowed_cidrs=["127.0.0.1/32"])
    assert scope.validate_target("127.0.0.1").value == "127.0.0.1"


def test_ip_outside_cidr_rejected():
    scope = make_scope(allowed_domains=[], allowed_cidrs=["127.0.0.1/32"])
    with pytest.raises(ScopeViolationError):
        scope.validate_target("10.0.0.1")


def test_active_scan_guard():
    scope = make_scope(active_scan=False)
    with pytest.raises(ScopeViolationError):
        scope.require_active_scan()
    make_scope(active_scan=True).require_active_scan()


def test_scope_roundtrip_dict():
    scope = make_scope(active_scan=True, created_by="local-admin")
    restored = Scope.from_dict(scope.to_dict())
    assert restored.to_dict() == scope.to_dict()


# ── 执行模式 ──────────────────────────────────────────────


def test_default_mode_is_mock():
    assert resolve_mode(None) == MODE_MOCK


def test_invalid_mode_rejected():
    with pytest.raises(Exception) as excinfo:
        resolve_mode("turbo")
    assert "mode" in str(excinfo.value)


def test_real_mode_blocked_without_env(monkeypatch):
    monkeypatch.delenv(REAL_SCAN_ENV, raising=False)
    with pytest.raises(ScopeViolationError):
        resolve_mode(MODE_REAL)


def test_real_mode_allowed_with_env(monkeypatch):
    monkeypatch.setenv(REAL_SCAN_ENV, "true")
    assert resolve_mode(MODE_REAL) == MODE_REAL


@pytest.mark.parametrize(
    "target,expected",
    [
        ("127.0.0.1", True),
        ("localhost", True),
        ("example.test", True),
        ("10.0.0.5", True),
        ("scanme.example.com", False),
    ],
)
def test_is_local_only_target(target, expected):
    assert is_local_only_target(target) is expected
