"""P0-2 单元测试：统一 Policy / Scope Engine（``core.policy``）。

方案第 5.3 节点名了四个必须重复确认 Scope 的时机，本文件逐个锁死：

* Job 创建      —— :func:`validate_job_targets`
* Step 执行前    —— :func:`validate_step_target`
* 目标解析后     —— :func:`validate_resolved_address`
* 重定向         —— :func:`validate_redirect_target`

全部用注入的假 resolver，**不发真实 DNS 请求**（AGENTS.md 硬约束：
默认只用 mock 与 127.0.0.1）。
"""

import pytest

from core.errors import BadRequestError, InvalidTargetError, ScopeViolationError
from core.policy import (
    is_dangerous_address,
    scope_address_allowed,
    validate_job_targets,
    validate_redirect_target,
    validate_resolved_address,
    validate_step_target,
)
from core.scope import Scope


def _scope(**overrides) -> Scope:
    payload = {
        "id": "scope_test",
        "name": "策略测试范围",
        "allowed_domains": ["example.test"],
        "allowed_cidrs": ["127.0.0.1/32"],
        "excluded_domains": [],
        "active_scan": False,
    }
    payload.update(overrides)
    return Scope(**payload)


@pytest.fixture
def scope_id(local_db):
    """真实落库的 Scope：四个入口都要能从库里读到它。"""
    from core import scope_store

    return scope_store.create(
        name="策略测试范围",
        allowed_domains=["example.test"],
        allowed_cidrs=["127.0.0.1/32"],
    ).id


# ── 地址分类 ──────────────────────────────────────────────


@pytest.mark.parametrize(
    "address,expected",
    [
        ("127.0.0.1", True),
        ("::1", True),
        ("10.0.0.5", True),
        ("192.168.1.1", True),
        ("172.16.0.1", True),
        ("169.254.169.254", True),   # 云元数据地址，最经典的 SSRF 目标
        ("0.0.0.0", True),
        ("224.0.0.1", True),
        ("fe80::1", True),
        ("8.8.8.8", False),
        ("1.1.1.1", False),
        ("not-an-ip", True),          # 解析不出地址时按可疑处理
    ],
)
def test_is_dangerous_address(address, expected):
    assert is_dangerous_address(address) is expected


# ── 入口 1：Job 创建 ──────────────────────────────────────


def test_validate_job_targets_requires_scope_id(scope_id):
    """无 Scope 一律拒绝（方案第 8 节第 3 条：Scope 缺失即拒绝）。"""
    with pytest.raises(BadRequestError) as excinfo:
        validate_job_targets(None, ["example.test"])
    assert "scope_id" in excinfo.value.message

    with pytest.raises(BadRequestError):
        validate_job_targets("   ", ["example.test"])


def test_validate_job_targets_unknown_scope_is_403():
    with pytest.raises(ScopeViolationError):
        validate_job_targets("scope_missing", ["example.test"])


def test_validate_job_targets_empty_targets_is_400(scope_id):
    with pytest.raises(BadRequestError):
        validate_job_targets(scope_id, [])


def test_validate_job_targets_returns_normalized_values(scope_id):
    scope, validated = validate_job_targets(scope_id, ["HTTPS://A.Example.TEST:8443/path?q=1"])
    assert scope.id == scope_id
    assert validated == ["a.example.test"]


def test_validate_job_targets_rejects_one_bad_target_in_many(scope_id):
    """多目标里只要一个越界就整体拒绝，不返回部分结果。"""
    with pytest.raises(ScopeViolationError) as excinfo:
        validate_job_targets(scope_id, ["a.example.test", "evil.test", "b.example.test"])
    assert "evil.test" in excinfo.value.message


def test_validate_job_targets_rejects_excluded_domain(scope_id):
    from core import scope_store

    strict = scope_store.create(
        name="带排除的范围",
        allowed_domains=["example.test"],
        excluded_domains=["secret.example.test"],
    ).id
    with pytest.raises(ScopeViolationError):
        validate_job_targets(strict, ["secret.example.test"])


def test_validate_job_targets_rejects_invalid_target(scope_id):
    with pytest.raises(InvalidTargetError):
        validate_job_targets(scope_id, ["not a host"])


def test_validate_job_targets_ip_must_be_in_cidr(scope_id):
    _, validated = validate_job_targets(scope_id, ["127.0.0.1"])
    assert validated == ["127.0.0.1"]

    with pytest.raises(ScopeViolationError):
        validate_job_targets(scope_id, ["10.0.0.1"])


# ── 入口 2：Step 执行前 ───────────────────────────────────


def test_validate_step_target_ok(scope_id):
    assert validate_step_target(scope_id, "A.Example.TEST") == "a.example.test"


def test_validate_step_target_rejects_out_of_scope(scope_id):
    """执行期复检：Scope 收紧后越界目标必须被拦下。"""
    with pytest.raises(ScopeViolationError):
        validate_step_target(scope_id, "evil.test")


def test_validate_step_target_rejects_missing_scope(scope_id):
    with pytest.raises(BadRequestError):
        validate_step_target(None, "example.test")


# ── 入口 3：解析后的地址 ──────────────────────────────────


def test_resolved_public_address_is_allowed():
    scope = _scope()
    resolved = validate_resolved_address(scope, "a.example.test", resolver=lambda host: ["93.184.216.34"])
    assert resolved == ["93.184.216.34"]


def test_resolved_loopback_is_rejected_unless_explicit():
    """域名在 Scope 内、但解析到环回地址 —— 必须拒绝（SSRF 核心场景）。"""
    # 这个 Scope 只放行域名，allowed_cidrs 为空：127.0.0.1 没有被显式放行。
    scope = _scope(allowed_cidrs=[])
    with pytest.raises(ScopeViolationError) as excinfo:
        validate_resolved_address(scope, "a.example.test", resolver=lambda host: ["127.0.0.1"])
    assert "127.0.0.1" in excinfo.value.message


def test_resolved_private_address_is_rejected():
    scope = _scope()
    with pytest.raises(ScopeViolationError):
        validate_resolved_address(scope, "a.example.test", resolver=lambda host: ["10.1.2.3"])


def test_resolved_metadata_address_is_rejected():
    """169.254.169.254（云元数据）同样属于受限地址。"""
    scope = _scope()
    with pytest.raises(ScopeViolationError):
        validate_resolved_address(scope, "a.example.test", resolver=lambda host: ["169.254.169.254"])


def test_resolved_dangerous_address_allowed_when_in_scope_cidrs():
    """本机 fixture 场景：127.0.0.1 被 allowed_cidrs 显式放行时才允许。"""
    scope = _scope(allowed_cidrs=["127.0.0.1/32"])
    resolved = validate_resolved_address(scope, "127.0.0.1", resolver=lambda host: ["127.0.0.1"])
    assert resolved == ["127.0.0.1"]


def test_resolution_failure_does_not_block():
    """解析不出来 = 无法判断，交给 Runner 报 network_error，不在这里拦。"""
    scope = _scope()
    assert validate_resolved_address(scope, "a.example.test", resolver=lambda host: []) == []


def test_resolver_exception_does_not_block():
    def _boom(host):
        raise OSError("DNS 不可用")

    scope = _scope()
    assert validate_resolved_address(scope, "a.example.test", resolver=_boom) == []


def test_resolved_mixed_addresses_rejected_if_any_is_dangerous():
    """DNS 轮询/多 A 记录：只要有一条落在受限网段就拒绝整个目标。"""
    scope = _scope()
    with pytest.raises(ScopeViolationError):
        validate_resolved_address(scope, "a.example.test", resolver=lambda host: ["93.184.216.34", "10.0.0.1"])


def test_scope_address_allowed():
    scope = _scope(allowed_cidrs=["10.0.0.0/8"])
    assert scope_address_allowed(scope, "10.1.1.1") is True
    assert scope_address_allowed(scope, "11.1.1.1") is False
    assert scope_address_allowed(scope, "not-an-ip") is False


# ── 入口 4：重定向 ────────────────────────────────────────


def test_redirect_within_scope_is_allowed():
    scope = _scope()
    assert validate_redirect_target(scope, "https://a.example.test/login") == "a.example.test"
    assert validate_redirect_target(scope, "http://example.test") == "example.test"


def test_redirect_out_of_scope_is_rejected():
    scope = _scope()
    with pytest.raises(ScopeViolationError) as excinfo:
        validate_redirect_target(scope, "https://evil.test/steal")
    assert "evil.test" in excinfo.value.message


def test_redirect_to_excluded_domain_is_rejected():
    scope = _scope(excluded_domains=["secret.example.test"])
    with pytest.raises(ScopeViolationError):
        validate_redirect_target(scope, "https://secret.example.test/")


@pytest.mark.parametrize("url", ["file:///etc/passwd", "gopher://evil.test:70/_x", "data:text/html,hi"])
def test_redirect_with_dangerous_scheme_is_rejected(url):
    scope = _scope()
    with pytest.raises(InvalidTargetError):
        validate_redirect_target(scope, url)


def test_redirect_relative_path_is_a_noop():
    """相对跳转没有主机，沿用原目标，返回空串表示「无需再校验」。"""
    scope = _scope()
    assert validate_redirect_target(scope, "/login") == ""


def test_redirect_empty_is_rejected():
    scope = _scope()
    with pytest.raises(InvalidTargetError):
        validate_redirect_target(scope, "   ")
