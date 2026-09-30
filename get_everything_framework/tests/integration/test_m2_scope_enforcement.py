"""M2 集成测试（二）：Scope 强制校验与 mock/real 分流。

对应方案 M2/M3 之间的关键约束：

* 无 Scope 创建 job 被拒绝；
* 目标越界被拒绝；
* mock 模式绝不调用真实外部工具；
* real 模式需要 Scope.active_scan + 环境开关双重确认。
"""

import io

import pytest


def _make_scope(admin_client, **overrides):
    payload = {
        "name": "本地测试范围",
        "allowed_domains": ["example.test"],
        "allowed_cidrs": ["127.0.0.1/32"],
        "active_scan": False,
    }
    payload.update(overrides)
    resp = admin_client.post("/api/scopes", json=payload)
    assert resp.status_code == 201, resp.get_json()
    return resp.get_json()["scope"]["id"]


def _upload(admin_client, content=b"a.example.test\nb.example.test\n"):
    return admin_client.post(
        "/api/upload",
        data={"file": (io.BytesIO(content), "targets.txt")},
        content_type="multipart/form-data",
    ).get_json()["upload_id"]


# ── 无 Scope 一律拒绝 ─────────────────────────────────────


def test_run_without_scope_id_is_rejected(admin_client):
    """M2 验收：无 Scope 创建 job 返回 400。"""
    resp = admin_client.post(
        "/api/run",
        json={"domain": "example.test", "tools": ["subfinder"], "mode": "mock"},
    )
    assert resp.status_code == 400
    body = resp.get_json()
    assert body["error_code"] == "bad_request"
    assert "scope_id" in body["error_message"]


def test_run_with_unknown_scope_id_is_rejected(admin_client):
    resp = admin_client.post(
        "/api/run",
        json={
            "domain": "example.test",
            "tools": ["subfinder"],
            "scope_id": "scope_does_not_exist",
            "mode": "mock",
        },
    )
    assert resp.status_code == 403
    assert resp.get_json()["error_code"] == "scope_violation"


def test_single_tool_run_without_scope_is_rejected(admin_client):
    resp = admin_client.post("/api/tool/subfinder/run", json={"domain": "example.test"})
    assert resp.status_code == 400


# ── 目标越界 ──────────────────────────────────────────────


def test_target_outside_scope_is_rejected(admin_client):
    scope_id = _make_scope(admin_client)
    resp = admin_client.post(
        "/api/run",
        json={"domain": "evil.test", "tools": ["subfinder"], "scope_id": scope_id, "mode": "mock"},
    )
    assert resp.status_code == 403
    body = resp.get_json()
    assert body["error_code"] == "scope_violation"
    assert "evil.test" in body["error_message"]


def test_excluded_domain_is_rejected(admin_client):
    scope_id = _make_scope(admin_client, excluded_domains=["secret.example.test"])
    resp = admin_client.post(
        "/api/run",
        json={
            "domain": "secret.example.test",
            "tools": ["subfinder"],
            "scope_id": scope_id,
            "mode": "mock",
        },
    )
    assert resp.status_code == 403


def test_upload_with_out_of_scope_targets_is_rejected(admin_client):
    """上传文件里的目标同样要过 Scope，且任一出界即整体拒绝。"""
    scope_id = _make_scope(admin_client)
    upload_id = _upload(admin_client, content=b"a.example.test\nevil.test\n")

    resp = admin_client.post(
        "/api/run",
        json={"upload_id": upload_id, "tools": ["subfinder"], "scope_id": scope_id, "mode": "mock"},
    )
    assert resp.status_code == 403
    assert "evil.test" in resp.get_json()["error_message"]


def test_ip_target_allowed_by_cidr_scope(admin_client):
    scope_id = _make_scope(admin_client, allowed_domains=[], allowed_cidrs=["127.0.0.1/32"])
    resp = admin_client.post(
        "/api/run",
        json={"domain": "127.0.0.1", "tools": ["httpx"], "scope_id": scope_id, "mode": "mock"},
    )
    assert resp.status_code == 200
    assert resp.get_json()["targets"] == ["127.0.0.1"]


# ── mock / real 分流 ──────────────────────────────────────


def test_mock_mode_returns_without_calling_real_tools(admin_client, monkeypatch):
    """mock 模式必须完全绕开真实 runner。"""
    import api.scan as scan_api
    import tool_runner

    def _explode(*args, **kwargs):  # pragma: no cover - 只要被调用就失败
        raise AssertionError("mock 模式不允许调用真实工具")

    monkeypatch.setattr(tool_runner, "run_tools", _explode)
    monkeypatch.setattr(scan_api, "run_tools", _explode)

    scope_id = _make_scope(admin_client)
    resp = admin_client.post(
        "/api/run",
        json={"domain": "example.test", "tools": ["subfinder"], "scope_id": scope_id, "mode": "mock"},
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["mode"] == "mock"
    assert body["outcomes"][0]["mock"] is True
    assert body["outcomes"][0]["status"] == "success"


def test_mock_mode_is_default(admin_client):
    scope_id = _make_scope(admin_client)
    resp = admin_client.post(
        "/api/run",
        json={"domain": "example.test", "tools": ["subfinder"], "scope_id": scope_id},
    )
    assert resp.status_code == 200
    assert resp.get_json()["mode"] == "mock"


def test_real_mode_blocked_without_env_switch(admin_client, monkeypatch):
    """真实扫描开关未开时，real 模式被拒绝。"""
    from core.safety import REAL_SCAN_ENV

    monkeypatch.delenv(REAL_SCAN_ENV, raising=False)
    scope_id = _make_scope(admin_client, active_scan=True)

    resp = admin_client.post(
        "/api/run",
        json={"domain": "example.test", "tools": ["subfinder"], "scope_id": scope_id, "mode": "real"},
    )
    assert resp.status_code == 403
    assert resp.get_json()["error_code"] == "scope_violation"


def test_real_mode_blocked_when_scope_disallows_active_scan(admin_client, monkeypatch):
    """开关打开了，但 Scope 声明 active_scan=false 时仍然拒绝。"""
    from core.safety import REAL_SCAN_ENV

    monkeypatch.setenv(REAL_SCAN_ENV, "true")
    scope_id = _make_scope(admin_client, active_scan=False)

    resp = admin_client.post(
        "/api/run",
        json={"domain": "example.test", "tools": ["subfinder"], "scope_id": scope_id, "mode": "real"},
    )
    assert resp.status_code == 403


def test_invalid_mode_is_rejected(admin_client):
    scope_id = _make_scope(admin_client)
    resp = admin_client.post(
        "/api/run",
        json={"domain": "example.test", "tools": ["subfinder"], "scope_id": scope_id, "mode": "turbo"},
    )
    assert resp.status_code == 400


# ── 目标数上限 ────────────────────────────────────────────


def test_too_many_targets_are_rejected(admin_client):
    from config import SCAN_LIMITS

    limit = SCAN_LIMITS["max_targets_per_job"]
    scope_id = _make_scope(admin_client, allowed_domains=["example.test"])
    lines = "\n".join(f"h{i}.example.test" for i in range(limit + 1)).encode()
    upload_id = _upload(admin_client, content=lines)

    resp = admin_client.post(
        "/api/run",
        json={"upload_id": upload_id, "tools": ["subfinder"], "scope_id": scope_id, "mode": "mock"},
    )
    assert resp.status_code == 400
    assert "最多" in resp.get_json()["error_message"]


# ── mock 场景可解释性（M4 语义提前固化） ──────────────────


@pytest.mark.parametrize(
    "scenario,status,error_code",
    [
        ("success", "success", None),
        # M4 起零结果不再与「成功」混同：状态仍是 success，但错误码为 no_results，
        # 前端才能显示「执行成功但未发现结果」而不是一个空表。
        ("empty", "success", "no_results"),
        ("tool_not_found", "failed", "tool_not_found"),
        ("timeout", "timeout", "timeout"),
        ("non_zero_exit", "failed", "unknown_error"),
        ("parse_error", "failed", "parse_error"),
        ("partial", "partial", "partial_success"),
    ],
)
def test_mock_scenarios_are_distinguishable(admin_client, scenario, status, error_code):
    scope_id = _make_scope(admin_client)
    resp = admin_client.post(
        "/api/run",
        json={
            "domain": "example.test",
            "tools": ["subfinder"],
            "scope_id": scope_id,
            "mode": "mock",
            "scenario": scenario,
        },
    )
    assert resp.status_code == 200
    outcome = resp.get_json()["outcomes"][0]
    assert outcome["status"] == status
    assert outcome["error_code"] == error_code


def test_empty_result_differs_from_failure(admin_client):
    """零结果与失败必须可区分（方案 M4 验收项）。"""
    scope_id = _make_scope(admin_client)

    empty = admin_client.post(
        "/api/run",
        json={
            "domain": "example.test",
            "tools": ["subfinder"],
            "scope_id": scope_id,
            "mode": "mock",
            "scenario": "empty",
        },
    ).get_json()["outcomes"][0]

    failed = admin_client.post(
        "/api/run",
        json={
            "domain": "example.test",
            "tools": ["subfinder"],
            "scope_id": scope_id,
            "mode": "mock",
            "scenario": "tool_not_found",
        },
    ).get_json()["outcomes"][0]

    # 两者都是 0 条，但语义完全不同：一个是「跑通了没东西」，一个是「没跑起来」。
    assert empty["found_count"] == 0 and empty["error_code"] == "no_results"
    assert empty["status"] == "success"
    assert failed["found_count"] == 0 and failed["error_code"] == "tool_not_found"
    assert failed["status"] == "failed"
