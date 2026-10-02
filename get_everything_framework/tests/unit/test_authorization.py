"""授权匹配试算（下一阶段方案第 4、6 节 Phase 2「选择授权」）。

这一层要回答的是用户最容易卡住的问题：**我到底缺了哪一道闸门**。
当前实现下「目标越界」「范围没开 active_scan」「环境总开关没开」都表现为同一个
``403 scope_violation``，用户只能靠读错误消息反推。

本文件锁死三件事：

1. 试算结论与真实提交**不可能不一致** —— 两者用的是同一个
   :meth:`core.scope.Scope.match_target`；
2. 试算**不写库、不发网络请求、不写审计** —— 它是查询，不是业务动作；
3. 「缺哪一道」的判定顺序正确（没范围 > 不在范围内 > 没开 active_scan > 没开环境开关）。

不碰网络：所有目标都用 RFC 6761 保留域 ``example.test`` 与 RFC 5737 文档保留段。
"""

import pytest

from core import audit, authorization, jobs as jobs_store, projects, scope_store
from core.authorization import (
    STATUS_EXCLUDED,
    STATUS_READY,
    STATUS_SCOPE_INACTIVE,
    TargetCheck,
)
from core.scope import Scope


def _make_scope(**overrides):
    payload = {
        "id": "scope_unit",
        "name": "学校官网",
        "allowed_domains": ["example.test"],
        "allowed_cidrs": [],
        "excluded_domains": [],
        "active_scan": False,
    }
    payload.update(overrides)
    return Scope(**payload)


# ── 纯逻辑：match_target 与 validate_target 同源 ────────────


def test_match_target_agrees_with_validate_target():
    """试算用的 ``match_target`` 与 Policy 用的 ``validate_target`` 必须同源。

    这两者一旦漂移，就会出现「试算说可以、提交却 403」这种最难查的不一致。
    这里对同一个 Scope 逐条比对两种调用的结论。
    """
    from core.errors import ScopeViolationError
    from core.scope import MATCH_ALLOWED, MATCH_EXCLUDED, MATCH_OUT_OF_SCOPE

    scope = _make_scope(excluded_domains=["secret.example.test"])
    cases = {
        "example.test": MATCH_ALLOWED,
        "a.example.test": MATCH_ALLOWED,
        "secret.example.test": MATCH_EXCLUDED,
        "notexample.test": MATCH_OUT_OF_SCOPE,
        "other.test": MATCH_OUT_OF_SCOPE,
    }
    for raw, expected in cases.items():
        _, verdict = scope.match_target(raw)
        assert verdict == expected, raw

        if expected == MATCH_ALLOWED:
            assert scope.validate_target(raw).value == raw
        else:
            with pytest.raises(ScopeViolationError):
                scope.validate_target(raw)


def test_match_target_still_raises_on_invalid_format():
    """格式非法仍然抛 ``InvalidTargetError`` —— 它不是「判定结果」而是输入错误。"""
    from core.errors import InvalidTargetError

    with pytest.raises(InvalidTargetError):
        _make_scope().match_target("not a host")


# ── 试算：五档结论 ─────────────────────────────────────────


def test_check_target_without_any_scope_reports_no_scope(app_module):
    """一个范围都没建时，blocker 必须是 ``no_scope``（而不是含糊的「未授权」）。"""
    result = authorization.check_target("www.example.test")
    assert isinstance(result, TargetCheck)
    assert result.valid is True
    assert result.candidates == 0
    assert result.matches == []
    assert result.blocker == "no_scope"
    assert result.ready is False


def test_check_target_out_of_scope_reports_not_authorized(app_module):
    """有范围但不覆盖 → ``not_authorized``，且不下发无关范围。"""
    scope_store.create(name="别人的站", allowed_domains=["other.example.test"], active_scan=True)

    result = authorization.check_target("www.example.test")
    assert result.candidates == 1
    assert result.matches == []
    assert result.blocker == "not_authorized"


def test_check_target_ready_when_scope_active_and_env_on(app_module, monkeypatch):
    """目标被授权 + 范围开了真实扫描 + 环境开关开着 → ready。"""
    monkeypatch.setenv("GEF_ALLOW_REAL_SCAN", "true")
    scope = scope_store.create(name="学校官网", allowed_domains=["example.test"], active_scan=True)

    result = authorization.check_target("www.example.test")
    assert result.ready is True
    assert result.blocker == ""
    assert len(result.matches) == 1
    assert result.matches[0].status == STATUS_READY
    assert result.matches[0].scope_id == scope.id
    assert result.matches[0].scope_name == "学校官网"
    assert result.matches[0].allowed_domains == ["example.test"]


def test_check_target_reports_scope_inactive(app_module, monkeypatch):
    """范围覆盖但没开 active_scan → ``scope_inactive``（即使环境开关是开的）。"""
    monkeypatch.setenv("GEF_ALLOW_REAL_SCAN", "true")
    scope_store.create(name="只读范围", allowed_domains=["example.test"], active_scan=False)

    result = authorization.check_target("www.example.test")
    assert result.blocker == "scope_inactive"
    assert result.matches[0].status == STATUS_SCOPE_INACTIVE
    assert result.matches[0].active_scan is False


def test_check_target_reports_env_disabled_last(app_module, monkeypatch):
    """范围允许但环境开关没开 → ``env_disabled``（这是最后一道闸）。"""
    monkeypatch.delenv("GEF_ALLOW_REAL_SCAN", raising=False)
    scope_store.create(name="学校官网", allowed_domains=["example.test"], active_scan=True)

    result = authorization.check_target("www.example.test")
    assert result.blocker == "env_disabled"
    assert result.real_scan_enabled is False
    # 范围本身是「已授权」的 —— 缺的只是环境开关，这一点必须能区分出来。
    assert result.matches[0].status == STATUS_READY


def test_check_target_excluded_beats_allowed(app_module, monkeypatch):
    """排除优先：命中 excluded_domains 时即使也在 allowed_domains 里也是拒绝。"""
    monkeypatch.setenv("GEF_ALLOW_REAL_SCAN", "true")
    scope_store.create(
        name="带排除的范围",
        allowed_domains=["example.test"],
        excluded_domains=["secret.example.test"],
        active_scan=True,
    )

    result = authorization.check_target("secret.example.test")
    assert result.blocker == "not_authorized"
    assert result.matches[0].status == STATUS_EXCLUDED
    assert result.ready is False
    # 排除项仍留在 matches 里（诊断价值），但**不构成授权** —— eligible 必须是空的。
    assert result.eligible == []
    assert result.to_dict()["eligible_scope_ids"] == []


def test_check_target_invalid_target_does_not_raise(app_module):
    """格式非法时返回 ``valid=False`` 而不是抛异常 —— 批量试算不该被第一个坏目标中断。"""
    result = authorization.check_target("not a host")
    assert result.valid is False
    assert result.blocker == "invalid_target"
    assert result.error_message
    assert result.normalized == ""


def test_check_targets_keeps_input_order_and_isolates_failures(app_module, monkeypatch):
    """批量试算：保持顺序，坏目标只影响它自己。"""
    monkeypatch.setenv("GEF_ALLOW_REAL_SCAN", "true")
    scope_store.create(name="学校官网", allowed_domains=["example.test"], active_scan=True)

    results = authorization.check_targets(
        ["www.example.test", "not a host", "other.test", "a.example.test"]
    )
    assert [item.raw for item in results] == [
        "www.example.test",
        "not a host",
        "other.test",
        "a.example.test",
    ]
    assert [item.ready for item in results] == [True, False, False, True]


def test_check_target_handles_ip_scope(app_module, monkeypatch):
    """IP 目标走 allowed_cidrs；网段内 ready、网段外 not_authorized。

    网段用 RFC 5737 文档保留段（不会分配给任何真实主机）。
    """
    monkeypatch.setenv("GEF_ALLOW_REAL_SCAN", "true")
    scope_store.create(
        name="文档保留网段", allowed_domains=[], allowed_cidrs=["192.0.2.0/24"], active_scan=True
    )

    inside = authorization.check_target("192.0.2.10")
    assert inside.kind == "ip"
    assert inside.ready is True

    outside = authorization.check_target("198.51.100.7")
    assert outside.ready is False
    assert outside.blocker == "not_authorized"


def test_check_target_reports_project_ownership(app_module, monkeypatch):
    """范围挂在哪个项目下要能显示出来（否则用户不知道该用哪个项目建任务）。"""
    monkeypatch.setenv("GEF_ALLOW_REAL_SCAN", "true")
    scope = scope_store.create(name="学校官网", allowed_domains=["example.test"], active_scan=True)
    project = projects.create(
        name="学校官网授权测试",
        authorization_note="2026-10-02 校方信息中心书面授权",
        scope_ids=[scope.id],
    )

    result = authorization.check_target("www.example.test")
    match = result.matches[0]
    assert match.project_id == project.id
    assert match.project_name == "学校官网授权测试"
    assert match.project_ids == [project.id]


def test_check_target_sorts_ready_first(app_module, monkeypatch):
    """多个范围命中时，能用的排前面 —— 用户先看到「可以扫的那个」。"""
    monkeypatch.setenv("GEF_ALLOW_REAL_SCAN", "true")
    scope_store.create(name="未开扫描", allowed_domains=["example.test"], active_scan=False)
    scope_store.create(name="已授权", allowed_domains=["example.test"], active_scan=True)

    result = authorization.check_target("www.example.test")
    assert [item.status for item in result.matches] == [STATUS_READY, STATUS_SCOPE_INACTIVE]


def test_check_target_does_not_write_anything(app_module, monkeypatch):
    """试算是**只读**的：不落任务、不写审计。

    这条是「试算不能被误当成业务动作」的可执行版本 —— 一旦有人图省事在试算里
    顺手建了任务或记了审计，这里就会红。
    """
    monkeypatch.setenv("GEF_ALLOW_REAL_SCAN", "true")
    scope_store.create(name="学校官网", allowed_domains=["example.test"], active_scan=True)

    before_jobs = len(jobs_store.list_jobs())
    before_events = len(audit.list_events(limit=500))

    for _ in range(3):
        authorization.check_target("www.example.test")
    authorization.check_targets(["a.example.test", "b.example.test"])

    assert len(jobs_store.list_jobs()) == before_jobs
    assert len(audit.list_events(limit=500)) == before_events


def test_check_target_declares_deferred_resolution(app_module, monkeypatch):
    """试算不做 DNS 解析，必须**如实声明**这一点，不能假装已经全查过。"""
    monkeypatch.setenv("GEF_ALLOW_REAL_SCAN", "true")
    scope_store.create(name="学校官网", allowed_domains=["example.test"], active_scan=True)

    result = authorization.check_target("www.example.test")
    assert result.resolved_check_deferred is True
    assert result.to_dict()["resolved_check_deferred"] is True


# ── 建议模式 ───────────────────────────────────────────────


def test_default_mode_prefers_real_only_when_all_targets_ready(app_module, monkeypatch):
    """全部目标都能扫 → 建议 real；只要有一个不行就建议 mock。

    这只是**建议**：真正生效的模式仍由 ``create_authorized_public_job`` 决定。
    """
    monkeypatch.setenv("GEF_ALLOW_REAL_SCAN", "true")
    scope_store.create(name="学校官网", allowed_domains=["example.test"], active_scan=True)

    all_ready = authorization.check_targets(["a.example.test", "b.example.test"])
    assert authorization.default_mode_for(all_ready) == "real"

    mixed = authorization.check_targets(["a.example.test", "other.test"])
    assert authorization.default_mode_for(mixed) == "mock"

    assert authorization.default_mode_for([]) == "mock"


def test_to_dict_shape_is_stable(app_module, monkeypatch):
    """``to_dict`` 的字段是前端契约，逐项锁死避免悄悄改名。"""
    monkeypatch.setenv("GEF_ALLOW_REAL_SCAN", "true")
    scope_store.create(name="学校官网", allowed_domains=["example.test"], active_scan=True)

    payload = authorization.check_target("www.example.test").to_dict()
    assert set(payload) == {
        "raw",
        "normalized",
        "kind",
        "valid",
        "error_message",
        "matches",
        "eligible_scope_ids",
        "candidates",
        "real_scan_enabled",
        "resolved_check_deferred",
        "ready",
        "blocker",
    }
    match = payload["matches"][0]
    assert set(match) == {
        "scope_id",
        "scope_name",
        "verdict",
        "status",
        "active_scan",
        "allowed_domains",
        "allowed_cidrs",
        "excluded_domains",
        "project_id",
        "project_name",
        "project_ids",
    }
